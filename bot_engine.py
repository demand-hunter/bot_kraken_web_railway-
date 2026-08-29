import time, json, os, threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from collections import deque
import pandas as pd
import numpy as np
import ccxt

# ---------------- CONFIG KRAKEN ----------------
EXCHANGE_ID = os.getenv('EXCHANGE_ID', 'kraken').lower()
SYMBOL = os.getenv('SYMBOL', 'BTC/USDT')
TIMEFRAME = os.getenv('TIMEFRAME', '5m')
HIGHER_TIMEFRAME = os.getenv('HIGHER_TIMEFRAME', '1h')
STARTING_BALANCE = float(os.getenv('STARTING_BALANCE', '1000'))
RISK_PER_TRADE = float(os.getenv('RISK_PER_TRADE', '0.005'))  # 0.5%
RR = float(os.getenv('RR', '1.5'))
LEVEL_LOOKBACK = int(os.getenv('LEVEL_LOOKBACK', '120'))
LEVEL_TOL = float(os.getenv('LEVEL_TOL', '0.0025'))
BREAKOUT_BUFFER = float(os.getenv('BREAKOUT_BUFFER', '0.0015'))
VOLUME_FACTOR = float(os.getenv('VOLUME_FACTOR', '1.25'))
POLL_SECONDS = int(os.getenv('POLL_SECONDS', '30'))
STATE_FILE = os.getenv('STATE_FILE', 'state.json')
TRADES_FILE = os.getenv('TRADES_FILE', 'trades.csv')

if not hasattr(ccxt, EXCHANGE_ID):
    raise RuntimeError(f'Exchange desconhecida no ccxt: {EXCHANGE_ID}')

exchange_class = getattr(ccxt, EXCHANGE_ID)
exchange = exchange_class({'enableRateLimit': True, 'timeout': 20000})

@dataclass
class Position:
    side: str
    entry: float
    stop: float
    target: float
    qty: float
    reason: str
    opened_at: str

class TradingBot:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.logs = deque(maxlen=250)
        self.balance, self.position, self.last_signal_candle = self.load_state()
        self.running = False
        self.last_price = None
        self.trend = '—'
        self.support = None
        self.resistance = None
        self.last_update = None
        self.last_error = None

    def ema(self, s, n):
        return s.ewm(span=n, adjust=False).mean()

    def atr(self, df, n=14):
        prev_close = df['close'].shift(1)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - prev_close).abs(),
            (df['low'] - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(n).mean()

    def fetch_df(self, symbol, timeframe, limit=300):
        rows = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(rows, columns=['ts','open','high','low','close','volume'])
        df['dt'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
        return df

    def trend_filter(self, htf):
        htf = htf.copy()
        htf['ema50'] = self.ema(htf['close'], 50)
        htf['ema200'] = self.ema(htf['close'], 200)
        r = htf.iloc[-2]
        if r['close'] > r['ema200'] and r['ema50'] > r['ema200']:
            return 'up'
        if r['close'] < r['ema200'] and r['ema50'] < r['ema200']:
            return 'down'
        return 'neutral'

    def horizontal_levels(self, df):
        w = df.iloc[-LEVEL_LOOKBACK-2:-2]
        return float(w['low'].min()), float(w['high'].max())

    def near(self, price, level, tol=LEVEL_TOL):
        return abs(price-level)/level <= tol

    def rejection_signal(self, df, support, resistance, trend):
        c = df.iloc[-2]
        prev = df.iloc[-3]
        body = abs(c['close']-c['open']) + 1e-9
        lower_wick = min(c['open'], c['close']) - c['low']
        upper_wick = c['high'] - max(c['open'], c['close'])
        buy = (trend == 'up' and self.near(c['low'], support) and c['close'] > support and
               lower_wick >= body * 0.6 and c['close'] >= prev['close'])
        sell = (trend == 'down' and self.near(c['high'], resistance) and c['close'] < resistance and
                upper_wick >= body * 0.6 and c['close'] <= prev['close'])
        return 'long' if buy else ('short' if sell else None)

    def breakout_retest_signal(self, df, support, resistance, trend):
        b = df.iloc[-3]
        r = df.iloc[-2]
        vol_ma = df['volume'].iloc[-23:-3].mean()
        if trend == 'up':
            broke = b['close'] > resistance*(1+BREAKOUT_BUFFER) and b['volume'] > vol_ma*VOLUME_FACTOR
            retest = r['low'] <= resistance*(1+LEVEL_TOL) and r['close'] > resistance
            if broke and retest:
                return 'long'
        if trend == 'down':
            broke = b['close'] < support*(1-BREAKOUT_BUFFER) and b['volume'] > vol_ma*VOLUME_FACTOR
            retest = r['high'] >= support*(1-LEVEL_TOL) and r['close'] < support
            if broke and retest:
                return 'short'
        return None

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    s = json.load(f)
                pos = Position(**s['position']) if s.get('position') else None
                return float(s.get('balance', STARTING_BALANCE)), pos, s.get('last_signal_candle')
            except Exception:
                pass
        return STARTING_BALANCE, None, None

    def save_state(self):
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'balance': self.balance,
                'position': asdict(self.position) if self.position else None,
                'last_signal_candle': self.last_signal_candle
            }, f, indent=2)

    def log_trade(self, row):
        pd.DataFrame([row]).to_csv(
            TRADES_FILE, mode='a',
            header=not os.path.exists(TRADES_FILE),
            index=False
        )

    def open_position(self, side, price, a, reason):
        dist = max(a*1.2, price*0.002)
        stop = price-dist if side == 'long' else price+dist
        target = price+dist*RR if side == 'long' else price-dist*RR
        risk_cash = self.balance*RISK_PER_TRADE
        qty = risk_cash/dist
        return Position(side, price, stop, target, qty, reason,
                        datetime.now(timezone.utc).isoformat())

    def check_exit(self, candle):
        pos = self.position
        if not pos:
            return
        if pos.side == 'long':
            stop_hit = candle['low'] <= pos.stop
            target_hit = candle['high'] >= pos.target
            if stop_hit:
                exit_price, result = pos.stop, 'LOSS'
            elif target_hit:
                exit_price, result = pos.target, 'WIN'
            else:
                return
            pnl = (exit_price-pos.entry)*pos.qty
        else:
            stop_hit = candle['high'] >= pos.stop
            target_hit = candle['low'] <= pos.target
            if stop_hit:
                exit_price, result = pos.stop, 'LOSS'
            elif target_hit:
                exit_price, result = pos.target, 'WIN'
            else:
                return
            pnl = (pos.entry-exit_price)*pos.qty

        new_balance = self.balance+pnl
        self.log_trade({
            'opened_at':pos.opened_at,
            'closed_at':datetime.now(timezone.utc).isoformat(),
            'symbol':SYMBOL,'side':pos.side,'entry':pos.entry,'exit':exit_price,
            'stop':pos.stop,'target':pos.target,'qty':pos.qty,'pnl':round(pnl,4),
            'balance':round(new_balance,4),'result':result,'reason':pos.reason
        })
        self.balance = new_balance
        self.position = None
        self.add_log(f'FECHOU {result} | PnL=R${pnl:.2f} | saldo=R${new_balance:.2f}')

    def add_log(self, msg):
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        line = f'{stamp} | {msg}'
        self.logs.appendleft(line)
        print(line, flush=True)

    def stats(self):
        wins = losses = trades = 0
        pnl = 0.0
        if os.path.exists(TRADES_FILE):
            try:
                t = pd.read_csv(TRADES_FILE)
                trades = len(t)
                wins = int((t['result'] == 'WIN').sum()) if 'result' in t else 0
                losses = int((t['result'] == 'LOSS').sum()) if 'result' in t else 0
                pnl = float(t['pnl'].sum()) if 'pnl' in t else 0.0
            except Exception:
                pass
        hit_rate = (wins / trades * 100.0) if trades else 0.0
        return {'trades': trades, 'wins': wins, 'losses': losses,
                'hit_rate': round(hit_rate, 2), 'pnl': round(pnl, 2)}

    def snapshot(self):
        with self.lock:
            return {
                'running': self.running,
                'exchange': EXCHANGE_ID,
                'symbol': SYMBOL,
                'timeframe': TIMEFRAME,
                'higher_timeframe': HIGHER_TIMEFRAME,
                'balance': round(self.balance, 2),
                'starting_balance': STARTING_BALANCE,
                'last_price': self.last_price,
                'trend': self.trend,
                'support': self.support,
                'resistance': self.resistance,
                'position': asdict(self.position) if self.position else None,
                'last_update': self.last_update,
                'last_error': self.last_error,
                'stats': self.stats(),
                'logs': list(self.logs)[:100],
                'mode': 'PAPER'
            }

    def start(self):
        with self.lock:
            if self.running:
                return False
            self.stop_event.clear()
            self.running = True
            self.thread = threading.Thread(target=self.run_loop, daemon=True)
            self.thread.start()
            self.add_log(f'Paper bot iniciado | fonte={EXCHANGE_ID} | {SYMBOL} {TIMEFRAME} | saldo fictício R${self.balance:.2f}')
            return True

    def stop(self):
        with self.lock:
            self.stop_event.set()
            self.running = False
            self.add_log('Bot pausado pelo usuário.')
            return True

    def reset(self):
        with self.lock:
            if self.running:
                return False, 'Pare o bot antes de resetar.'
            self.balance = STARTING_BALANCE
            self.position = None
            self.last_signal_candle = None
            self.last_price = None
            self.trend = '—'
            self.support = None
            self.resistance = None
            self.last_update = None
            self.last_error = None
            self.logs.clear()
            if os.path.exists(STATE_FILE):
                os.remove(STATE_FILE)
            if os.path.exists(TRADES_FILE):
                os.remove(TRADES_FILE)
            self.save_state()
            self.add_log(f'Simulação resetada para R${STARTING_BALANCE:.2f}.')
            return True, 'Reset concluído.'

    def run_loop(self):
        while not self.stop_event.is_set():
            try:
                df = self.fetch_df(SYMBOL, TIMEFRAME, 350)
                htf = self.fetch_df(SYMBOL, HIGHER_TIMEFRAME, 260)
                df['atr'] = self.atr(df)
                support, resistance = self.horizontal_levels(df)
                trend = self.trend_filter(htf)
                closed = df.iloc[-2]

                with self.lock:
                    self.last_price = round(float(closed['close']), 2)
                    self.support = round(support, 2)
                    self.resistance = round(resistance, 2)
                    self.trend = trend
                    self.last_update = datetime.now(timezone.utc).isoformat()
                    self.last_error = None

                    if self.position:
                        self.check_exit(closed)

                    candle_id = str(closed['dt'])
                    if not self.position and candle_id != self.last_signal_candle:
                        sig = self.rejection_signal(df, support, resistance, trend)
                        reason = 'rejection'
                        if not sig:
                            sig = self.breakout_retest_signal(df, support, resistance, trend)
                            reason = 'breakout_retest'

                        if sig and np.isfinite(closed['atr']):
                            self.position = self.open_position(
                                sig, float(closed['close']),
                                float(closed['atr']), reason
                            )
                            self.last_signal_candle = candle_id
                            self.add_log(
                                f'ABRIU {sig.upper()} {reason} @ {self.position.entry:.2f} | '
                                f'stop {self.position.stop:.2f} | alvo {self.position.target:.2f} | '
                                f'tend={trend} S={support:.2f} R={resistance:.2f}'
                            )
                        else:
                            self.add_log(
                                f'{closed["dt"]} | sem entrada | tend={trend} '
                                f'S={support:.2f} R={resistance:.2f}'
                            )
                            self.last_signal_candle = candle_id
                    self.save_state()

            except Exception as e:
                with self.lock:
                    self.last_error = repr(e)
                self.add_log(f'Erro: {repr(e)}')

            self.stop_event.wait(POLL_SECONDS)

        with self.lock:
            self.running = False
