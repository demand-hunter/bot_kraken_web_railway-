import time, json, os, threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from collections import deque
import pandas as pd
import numpy as np
import ccxt
from duration_clock import DurationShadowClock
from tie_activity_filter import TieActivityFilter

# ---------------- CONFIG KRAKEN / V4 ALPHA2 ----------------
EXCHANGE_ID = os.getenv('EXCHANGE_ID', 'kraken').lower()
# SYMBOL remains only as backwards-compatible/fallback symbol.
SYMBOL = os.getenv('SYMBOL', 'BTC/USDT')
DECISION_TIMEFRAME = os.getenv('DECISION_TIMEFRAME', '15m')
HIGHER_TIMEFRAME = os.getenv('HIGHER_TIMEFRAME', '1h')
STARTING_BALANCE = float(os.getenv('STARTING_BALANCE', '1000'))
RISK_PER_TRADE = float(os.getenv('RISK_PER_TRADE', '0.005'))  # unchanged: 0.5%
RR = float(os.getenv('RR', '1.5'))                            # unchanged
LEVEL_LOOKBACK = int(os.getenv('LEVEL_LOOKBACK', '120'))
LEVEL_TOL = float(os.getenv('LEVEL_TOL', '0.0025'))
BREAKOUT_BUFFER = float(os.getenv('BREAKOUT_BUFFER', '0.0015'))
VOLUME_FACTOR = float(os.getenv('VOLUME_FACTOR', '1.25'))
POLL_SECONDS = int(os.getenv('POLL_SECONDS', '30'))
STATE_FILE = os.getenv('STATE_FILE', 'state.json')
TRADES_FILE = os.getenv('TRADES_FILE', 'trades.csv')
DURATION_SHADOW_FILE = os.getenv('DURATION_SHADOW_FILE', 'duration_shadow.json')
DURATION_RESULTS_FILE = os.getenv('DURATION_RESULTS_FILE', 'duration_shadow.csv')
DURATION_HORIZONS = (5, 10, 15, 20, 30, 45, 60)
RADAR_SIZE = int(os.getenv('RADAR_SIZE', '100'))
FOCUS_SIZE = int(os.getenv('FOCUS_SIZE', '25'))
RADAR_BATCH = int(os.getenv('RADAR_BATCH', '5'))
RADAR_REFRESH_SECONDS = int(os.getenv('RADAR_REFRESH_SECONDS', '30'))
FOCUS_STICKINESS = float(os.getenv('FOCUS_STICKINESS', '2.0'))

if not hasattr(ccxt, EXCHANGE_ID):
    raise RuntimeError(f'Exchange desconhecida no ccxt: {EXCHANGE_ID}')

exchange_class = getattr(ccxt, EXCHANGE_ID)
exchange = exchange_class({'enableRateLimit': True, 'timeout': 20000})

# Preference list only seeds discovery. Kraken availability is resolved at runtime.
PREFERRED_BASES = [
    'BTC','ETH','SOL','XRP','DOGE','ADA','AVAX','LINK','DOT','LTC','BCH','TRX','NEAR','AAVE','UNI','ATOM','ETC','FIL','ARB','OP',
    'INJ','SUI','APT','HBAR','ICP','SHIB','PEPE','FET','RENDER','TAO','SEI','TIA','IMX','GRT','MKR','RUNE','STX','ALGO','VET','XLM',
    'EOS','THETA','EGLD','SAND','MANA','AXS','GALA','FLOW','KAVA','KSM','ZEC','DASH','COMP','SNX','CRV','LDO','DYDX','GMX','1INCH',
    'ENS','CHZ','ENJ','BAT','ZIL','IOTA','QTUM','NEO','ONT','ANKR','CELO','MASK','MINA','ROSE','JASMY','CFX','ORDI','BONK','FLOKI',
    'JUP','PYTH','STRK','WLD','ARKM','PENDLE','NOT','ENA','ZRO','JTO','BLUR','APE','GMT','POL','KAS','XTZ','KNC','YFI','SUSHI','OCEAN','LRC'
]

@dataclass
class Position:
    side: str
    entry: float
    stop: float
    target: float
    qty: float
    reason: str
    opened_at: str
    symbol: str = SYMBOL

class TradingBot:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.logs = deque(maxlen=400)
        self.balance, self.position, self.last_signal_candle = self.load_state()
        self.running = False
        self.last_price = None
        self.trend = '—'
        self.support = None
        self.resistance = None
        self.last_update = None
        self.last_error = None
        self.universe = []
        self.radar_scores = {}
        self.radar_meta = {}
        self.radar_cursor = 0
        self.focus = []
        self.last_radar_at = 0.0
        self.last_decision_slot = None
        self.scan_count = 0
        self.decision_count = 0
        self.near_signals = []
        self.shadow_levels = {}
        self.duration_clock = DurationShadowClock(self.fetch_df, self.add_log)
        self.tie_activity_filter = TieActivityFilter(DURATION_RESULTS_FILE)


    # Duration SHADOW lives in duration_clock.py (observer only).

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

    # -------- Universe / radar: 100 light, 25 deep --------
    def discover_universe(self):
        try:
            markets = exchange.load_markets()
            candidates = []
            for sym, m in markets.items():
                if not m.get('spot', True) or m.get('active') is False:
                    continue
                quote = (m.get('quote') or '').upper()
                base = (m.get('base') or '').upper()
                if quote not in ('USDT', 'USD') or not base:
                    continue
                # Exclude tokenized/staked/special pairs where possible.
                if any(x in base for x in ('.S','2L','2S','3L','3S','BULL','BEAR')):
                    continue
                candidates.append((sym, base, quote))

            pref_index = {b:i for i,b in enumerate(PREFERRED_BASES)}
            candidates.sort(key=lambda x: (
                0 if x[2] == 'USDT' else 1,
                pref_index.get(x[1], 10_000),
                x[0]
            ))
            unique = []
            seen_base = set()
            for sym, base, quote in candidates:
                # One quote per base keeps the 100-pair radar diverse.
                if base in seen_base:
                    continue
                seen_base.add(base)
                unique.append(sym)
                if len(unique) >= RADAR_SIZE:
                    break
            if SYMBOL in markets and SYMBOL not in unique:
                unique = [SYMBOL] + unique
                unique = unique[:RADAR_SIZE]
            self.universe = unique
            self.add_log(f'RADAR V4 | universo descoberto={len(self.universe)} | foco dinâmico={min(FOCUS_SIZE,len(self.universe))}')
        except Exception as e:
            self.universe = [SYMBOL]
            self.add_log(f'RADAR V4 | falha na descoberta, fallback {SYMBOL} | {repr(e)}')

    def _trend_context(self, df, fast=21, slow=50):
        if df is None or len(df) < slow + 8:
            return {'direction':'neutral','score':0.0,'slope':0.0,'sep':0.0}
        d = df.iloc[:-1].copy() if len(df) > 1 else df.copy()  # completed candles only
        ef = self.ema(d['close'], fast)
        es = self.ema(d['close'], slow)
        av = self.atr(d)
        a = max(float(av.iloc[-1]), 1e-12)
        slope = float(ef.iloc[-1] - ef.iloc[-4]) / a
        sep = abs(float(ef.iloc[-1] - es.iloc[-1])) / a
        c = float(d['close'].iloc[-1])
        if c > ef.iloc[-1] > es.iloc[-1] and slope > 0:
            direction = 'up'
        elif c < ef.iloc[-1] < es.iloc[-1] and slope < 0:
            direction = 'down'
        else:
            direction = 'neutral'
        score = min(100.0, abs(slope)*25 + sep*35)
        return {'direction':direction,'score':round(score,2),'slope':round(slope,3),'sep':round(sep,3)}

    def confirmed_trend(self, df15, h1):
        """The V4 gate: 1H context + 15M confirmation, both on completed candles."""
        c1 = self._trend_context(h1, 21, 50)
        c15 = self._trend_context(df15, 21, 50)
        direction = c1['direction'] if c1['direction'] == c15['direction'] else 'neutral'
        if direction == 'neutral':
            return 'neutral', 0.0, c1, c15
        quality = min(100.0, 0.55*c1['score'] + 0.45*c15['score'] + 20.0)
        return direction, round(quality,2), c1, c15

    def horizontal_levels(self, df):
        # df may contain the current candle; levels deliberately exclude it and the latest completed trigger candle.
        d = df.iloc[:-1].copy() if len(df) > 1 else df.copy()
        w = d.iloc[-LEVEL_LOOKBACK-2:-2]
        if len(w) < 10:
            w = d.iloc[:-2]
        return float(w['low'].min()), float(w['high'].max())

    def near(self, price, level, tol=LEVEL_TOL):
        return abs(price-level)/max(abs(level),1e-12) <= tol

    def radar_score(self, symbol):
        try:
            df = self.fetch_df(symbol, DECISION_TIMEFRAME, 140)
            if len(df) < 60:
                raise ValueError('histórico insuficiente')
            d = df.iloc[:-1].reset_index(drop=True)  # completed 15M only
            ef = self.ema(d['close'],21); es = self.ema(d['close'],50); av = self.atr(d)
            a = max(float(av.iloc[-1]),1e-12); price=float(d['close'].iloc[-1])
            trend_alignment = min(100.0, abs(float(ef.iloc[-1]-es.iloc[-1]))/a*35)
            prior = d.iloc[-42:-2]
            sup,res=float(prior['low'].min()),float(prior['high'].max())
            level_dist=min(abs(price-sup),abs(res-price))/a
            level_proximity=max(0.0,min(100.0,100-level_dist*55))
            atr_pct=a/max(price,1e-12)*100
            volatility=min(100.0,atr_pct*450)
            vr=float(d['volume'].iloc[-1])/max(float(d['volume'].tail(20).mean()),1e-12)
            volume=min(100.0,vr*60)
            score=.35*trend_alignment+.35*level_proximity+.15*volatility+.15*volume
            return round(max(0.0,min(100.0,score)),2), {
                'symbol':symbol,'score':round(score,2),'trend':round(trend_alignment,2),
                'level':round(level_proximity,2),'volatility':round(volatility,2),'volume':round(volume,2),
                'atr_pct':round(atr_pct,6),'price':round(price,8),'error':None
            }
        except Exception as e:
            return 0.0, {'symbol':symbol,'score':0.0,'error':str(e)}

    def _adjusted_radar_score(self, symbol, old_focus=None):
        old_focus = old_focus if old_focus is not None else set(self.focus)
        return (
            self.radar_scores.get(symbol, 0.0)
            - self.tie_activity_filter.penalty(symbol)
            + (FOCUS_STICKINESS if symbol in old_focus else 0.0)
        )

    def _ranking_order(self, old_focus=None):
        old_focus = old_focus if old_focus is not None else set(self.focus)
        return sorted(
            self.radar_scores,
            key=lambda s: self._adjusted_radar_score(s, old_focus),
            reverse=True,
        )

    def audit_ranking_boundary(self):
        """LAB D: grava ranks 21-35 dentro do lab_history.csv já existente."""
        if not self.radar_scores:
            return

        ranked = self._ranking_order(set(self.focus))

        # O estudo D só faz sentido quando pelo menos 35 pares já receberam score real.
        # Após um restart o radar aquece em lotes; nesse período não inventamos ranks.
        if len(ranked) < 35:
            return

        stamp = datetime.now(timezone.utc).isoformat()
        lab = getattr(self.duration_clock, "lab_history", None)
        if lab is None:
            return

        for idx in range(20, 35):
            sym = ranked[idx]
            meta = self.radar_meta.get(sym, {}) or {}
            penalty = self.tie_activity_filter.penalty(sym)
            raw_score = float(self.radar_scores.get(sym, 0.0))
            adjusted_score = float(self._adjusted_radar_score(sym, set(self.focus)))

            context = {
                "sample_type": "ranking_audit",
                "study": "D",
                "rank": idx + 1,
                "band": "focus_tail" if idx < FOCUS_SIZE else "substitute",
                "in_focus": sym in self.focus,
                "raw_score": round(raw_score, 4),
                "tie_penalty": round(float(penalty), 4),
                "adjusted_score": round(adjusted_score, 4),
                "atr_pct": meta.get("atr_pct"),
                "volatility_score": meta.get("volatility"),
                "volume_score": meta.get("volume"),
                "trend_score": meta.get("trend"),
                "level_score": meta.get("level"),
                "radar_score": raw_score,
                "decision_timeframe": DECISION_TIMEFRAME,
            }

            lab.register_sample({
                "id": f"ranking|{stamp}|{idx + 1}|{sym}",
                "sample_type": "ranking_audit",
                "opened_at": stamp,
                "symbol": sym,
                "side": "",
                "entry": meta.get("price"),
                "reason": "ranking_boundary",
                "context": context,
            })

    def refresh_radar_batch(self):
        if not self.universe:
            self.discover_universe()
        if not self.universe:
            return
        n = min(RADAR_BATCH, len(self.universe))
        for _ in range(n):
            sym = self.universe[self.radar_cursor % len(self.universe)]
            self.radar_cursor = (self.radar_cursor + 1) % len(self.universe)
            score, meta = self.radar_score(sym)
            self.radar_scores[sym] = score
            self.radar_meta[sym] = meta
            self.scan_count += 1

        old_focus = set(self.focus)
        ranked = self._ranking_order(old_focus)
        # During warmup fill missing places from yet-unscanned universe, but scanned pairs outrank seeds.
        seeds = [s for s in self.universe if s not in ranked]
        self.focus = (ranked + seeds)[:min(FOCUS_SIZE,len(self.universe))]
        self.last_radar_at = time.time()

    # -------- 15M structure + triggers --------
    def rejection_signal(self, df, support, resistance, trend):
        c = df.iloc[-2]   # latest completed 15M candle
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

    def shadow_level_events(self, symbol, support, resistance, closed):
        """Observation only: never authorizes/blocks a trade in alpha2."""
        prev = self.shadow_levels.get(symbol)
        events = []
        close = float(closed['close'])
        if prev:
            old_s, old_r = prev['support'], prev['resistance']
            if close > old_r*(1+BREAKOUT_BUFFER):
                events.append(f'R->{old_r:.8g} ROMPIDA (shadow)')
            if close < old_s*(1-BREAKOUT_BUFFER):
                events.append(f'S->{old_s:.8g} ROMPIDO (shadow)')
        self.shadow_levels[symbol] = {'support':support,'resistance':resistance,'close':close}
        return events

    def evaluate_symbol(self, symbol):
        df = self.fetch_df(symbol, DECISION_TIMEFRAME, max(160, LEVEL_LOOKBACK+10))
        h1 = self.fetch_df(symbol, HIGHER_TIMEFRAME, 120)
        if len(df) < max(60, LEVEL_LOOKBACK+5) or len(h1) < 60:
            return None
        df['atr'] = self.atr(df)
        support, resistance = self.horizontal_levels(df)
        trend, trend_q, c1, c15 = self.confirmed_trend(df, h1)
        closed = df.iloc[-2]
        candle_id = str(closed['dt'])
        atrv = float(closed['atr']) if np.isfinite(closed['atr']) else np.nan
        sig = None; reason = None
        if trend != 'neutral':
            sig = self.rejection_signal(df, support, resistance, trend)
            reason = 'rejection' if sig else None
            if not sig:
                sig = self.breakout_retest_signal(df, support, resistance, trend)
                reason = 'breakout_retest' if sig else None

        # Telemetry: how near this symbol was to the final gate; observation only.
        price = float(closed['close'])
        level_dist = min(abs(price-support),abs(resistance-price))/max(atrv,1e-12) if np.isfinite(atrv) else 99
        gate_checks = {
            '1h_direction': c1['direction'] != 'neutral',
            '15m_alignment': c15['direction'] == c1['direction'] and c15['direction'] != 'neutral',
            'near_level': level_dist <= 1.0,
            'confirmed_trend': trend != 'neutral',
            'trigger': bool(sig),
        }
        gate = sum(1 for ok in gate_checks.values() if ok)
        missing = [name for name, ok in gate_checks.items() if not ok]
        return {
            'symbol':symbol,'df':df,'closed':closed,'candle_id':candle_id,'atr':atrv,
            'support':support,'resistance':resistance,'trend':trend,'trend_quality':trend_q,
            'c1':c1,'c15':c15,'signal':sig,'reason':reason,'gate':gate,'missing':missing,'level_dist_atr':round(level_dist,3),
            'radar_score':self.radar_scores.get(symbol,0.0),'gate_checks':gate_checks,
            'shadow_events':self.shadow_level_events(symbol,support,resistance,closed)
        }

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    s = json.load(f)
                raw = s.get('position')
                if raw:
                    raw = dict(raw)
                    raw.setdefault('symbol', SYMBOL)  # backward compatibility with alpha1 state
                    pos = Position(**raw)
                else:
                    pos = None
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
        pd.DataFrame([row]).to_csv(TRADES_FILE, mode='a', header=not os.path.exists(TRADES_FILE), index=False)

    def open_position(self, symbol, side, price, a, reason):
        # Risk engine deliberately unchanged from baseline.
        dist = max(a*1.2, price*0.002)
        stop = price-dist if side == 'long' else price+dist
        target = price+dist*RR if side == 'long' else price-dist*RR
        risk_cash = self.balance*RISK_PER_TRADE
        qty = risk_cash/dist
        return Position(side, price, stop, target, qty, reason, datetime.now(timezone.utc).isoformat(), symbol)

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
            'opened_at':pos.opened_at,'closed_at':datetime.now(timezone.utc).isoformat(),
            'symbol':pos.symbol,'side':pos.side,'entry':pos.entry,'exit':exit_price,
            'stop':pos.stop,'target':pos.target,'qty':pos.qty,'pnl':round(pnl,4),
            'balance':round(new_balance,4),'result':result,'reason':pos.reason,
            'decision_timeframe':DECISION_TIMEFRAME
        })
        self.balance = new_balance
        self.position = None
        self.add_log(f'FECHOU {result} {pos.symbol} | PnL=R${pnl:.2f} | saldo=R${new_balance:.2f}')

    def add_log(self, msg):
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        line = f'{stamp} | {msg}'
        self.logs.appendleft(line)
        print(line, flush=True)

    def stats(self):
        wins = losses = trades = 0; pnl = 0.0
        if os.path.exists(TRADES_FILE):
            try:
                t = pd.read_csv(TRADES_FILE); trades = len(t)
                wins = int((t['result'] == 'WIN').sum()) if 'result' in t else 0
                losses = int((t['result'] == 'LOSS').sum()) if 'result' in t else 0
                pnl = float(t['pnl'].sum()) if 'pnl' in t else 0.0
            except Exception:
                pass
        hit_rate = (wins / trades * 100.0) if trades else 0.0
        avg_win = avg_loss = 0.0
        if os.path.exists(TRADES_FILE):
            try:
                t = pd.read_csv(TRADES_FILE)
                avg_win = float(t.loc[t.result=='WIN','pnl'].mean()) if (t.result=='WIN').any() else 0.0
                avg_loss = abs(float(t.loc[t.result=='LOSS','pnl'].mean())) if (t.result=='LOSS').any() else 0.0
            except Exception: pass
        return {'trades':trades,'wins':wins,'losses':losses,'hit_rate':round(hit_rate,2),'pnl':round(pnl,2),
                'avg_win':round(avg_win,2),'avg_loss':round(avg_loss,2)}

    def radar_snapshot(self):
        rows = []
        for s in self.focus:
            row = dict(self.radar_meta.get(s, {'symbol':s,'score':self.radar_scores.get(s,0.0),'error':None}))
            # Diagnostic metadata only; existing UI may ignore these fields.
            row['tie_penalty'] = self.tie_activity_filter.penalty(s)
            row['tie_activity'] = self.tie_activity_filter.meta(s)
            rows.append(row)
        return {'universe_size':len(self.universe),'focus_size':len(self.focus),'scan_count':self.scan_count,'focus':rows}

    def snapshot(self):
        with self.lock:
            display_symbol = self.position.symbol if self.position else (self.focus[0] if self.focus else SYMBOL)
            return {
                'running':self.running,'exchange':EXCHANGE_ID,'symbol':display_symbol,
                'timeframe':DECISION_TIMEFRAME,'decision_timeframe':DECISION_TIMEFRAME,'higher_timeframe':HIGHER_TIMEFRAME,
                'balance':round(self.balance,2),'starting_balance':STARTING_BALANCE,
                'last_price':self.last_price,'trend':self.trend,'support':self.support,'resistance':self.resistance,
                'position':asdict(self.position) if self.position else None,'last_update':self.last_update,
                'last_error':self.last_error,'stats':self.stats(),'logs':list(self.logs)[:120],'mode':'PAPER',
                'radar':self.radar_snapshot(),'near_signals':self.near_signals[:10],
                'architecture':'V4 alpha2 | Radar 100 -> Top 25 | decisão 15M | Tendência Confirmada + S/R'
            }

    def start(self):
        with self.lock:
            if self.running: return False
            self.stop_event.clear(); self.running=True
            self.thread=threading.Thread(target=self.run_loop,daemon=True); self.thread.start()
            self.add_log(f'V4 alpha2 iniciado | PAPER | decisão={DECISION_TIMEFRAME} | radar={RADAR_SIZE}->Top{FOCUS_SIZE} | saldo R${self.balance:.2f}')
            return True

    def stop(self):
        with self.lock:
            self.stop_event.set(); self.running=False; self.add_log('Bot pausado pelo usuário.'); return True

    def reset(self):
        with self.lock:
            if self.running: return False, 'Pare o bot antes de resetar.'
            self.balance=STARTING_BALANCE; self.position=None; self.last_signal_candle=None
            self.last_price=None; self.trend='—'; self.support=None; self.resistance=None
            self.last_update=None; self.last_error=None; self.logs.clear(); self.near_signals=[]
            if os.path.exists(STATE_FILE): os.remove(STATE_FILE)
            if os.path.exists(TRADES_FILE): os.remove(TRADES_FILE)
            self.duration_clock.reset()
            self.save_state(); self.add_log(f'Simulação resetada para R${STARTING_BALANCE:.2f}.')
            return True, 'Reset concluído.'

    def _check_open_position_market(self):
        if not self.position: return
        df = self.fetch_df(self.position.symbol, DECISION_TIMEFRAME, 5)
        if len(df) >= 2:
            self.check_exit(df.iloc[-2])

    def run_decision_cycle(self):
        if not self.focus: return
        # LAB D: registra ranks 21-35 no lab_history.csv, sem afetar o ranking.
        self.audit_ranking_boundary()
        candidates=[]; near=[]
        top_display=None
        for sym in list(self.focus):
            try:
                ev=self.evaluate_symbol(sym)
                if not ev: continue
                if top_display is None: top_display=ev
                if ev['gate'] >= 3:
                    near.append({k:ev[k] for k in ('symbol','gate','trend','trend_quality','level_dist_atr','radar_score')})
                for se in ev['shadow_events']:
                    self.add_log(f'{sym} | {se}')

                # LAB only: 4/5 is observed, never opens a PAPER position.
                if ev['gate'] == 4:
                    shadow_dir = ev.get('trend')
                    direction_source = 'confirmed_trend'
                    if shadow_dir not in ('up', 'down'):
                        shadow_dir = ev.get('c15', {}).get('direction')
                        direction_source = '15m_direction'
                    if shadow_dir not in ('up', 'down'):
                        shadow_dir = ev.get('c1', {}).get('direction')
                        direction_source = '1h_direction'

                    if shadow_dir in ('up', 'down'):
                        side = 'long' if shadow_dir == 'up' else 'short'
                        opened_at = str(pd.Timestamp(ev['closed']['dt']) + pd.Timedelta(minutes=15))
                        self.duration_clock.register(
                            symbol=ev['symbol'],
                            side=side,
                            entry=float(ev['closed']['close']),
                            opened_at=opened_at,
                            reason='4of5_shadow',
                            context={
                                'sample_type': '4of5_shadow',
                                'missing': ev.get('missing', []),
                                'direction_source': direction_source,
                                'trend': ev.get('trend'),
                                'trend_quality': ev.get('trend_quality'),
                                'direction_1h': ev.get('c1', {}).get('direction'),
                                'direction_15m': ev.get('c15', {}).get('direction'),
                                'gate': ev.get('gate'),
                                'gate_checks': ev.get('gate_checks', {}),
                                'trigger_present': bool(ev.get('signal')),
                                'support': ev.get('support'),
                                'resistance': ev.get('resistance'),
                                'level_dist_atr': ev.get('level_dist_atr'),
                                'atr': ev.get('atr'),
                                'radar_score': ev.get('radar_score'),
                                'shadow_events': ev.get('shadow_events', []),
                                'decision_timeframe': DECISION_TIMEFRAME,
                            },
                            sample_id=f"4of5|{ev['symbol']}|{ev['candle_id']}",
                        )

                if ev['signal'] and np.isfinite(ev['atr']):
                    candidates.append(ev)
            except Exception as e:
                self.radar_meta.setdefault(sym, {'symbol':sym})['error']=str(e)

        near.sort(key=lambda x:(x['gate'],x['trend_quality'],x['radar_score']), reverse=True)
        self.near_signals=near[:10]
        if top_display:
            self.last_price=round(float(top_display['closed']['close']),8)
            self.trend=top_display['trend']; self.support=round(top_display['support'],8); self.resistance=round(top_display['resistance'],8)

        if self.position:
            return
        if candidates:
            candidates.sort(key=lambda e:(e['trend_quality'],e['radar_score']), reverse=True)
            best=candidates[0]
            global_id=f"{best['symbol']}|{best['candle_id']}"
            if global_id != self.last_signal_candle:
                self.position=self.open_position(best['symbol'],best['signal'],float(best['closed']['close']),float(best['atr']),best['reason'])
                self.duration_clock.register(
                    symbol=best['symbol'],
                    side=self.position.side,
                    entry=float(self.position.entry),
                    opened_at=self.position.opened_at,
                    reason=self.position.reason,
                    context={
                        'sample_type': '5of5_real',
                        'missing': [],
                        'trend': best.get('trend'),
                        'trend_quality': best.get('trend_quality'),
                        'direction_1h': best.get('c1', {}).get('direction'),
                        'direction_15m': best.get('c15', {}).get('direction'),
                        'gate': best.get('gate'),
                        'gate_checks': best.get('gate_checks', {}),
                        'trigger_present': bool(best.get('signal')),
                        'support': best.get('support'),
                        'resistance': best.get('resistance'),
                        'level_dist_atr': best.get('level_dist_atr'),
                        'atr': best.get('atr'),
                        'radar_score': best.get('radar_score'),
                        'shadow_events': best.get('shadow_events', []),
                        'decision_timeframe': DECISION_TIMEFRAME,
                    },
                    sample_id=f"5of5|{best['symbol']}|{best['candle_id']}",
                )
                self.last_signal_candle=global_id
                self.add_log(
                    f"ABRIU {best['signal'].upper()} {best['symbol']} {best['reason']} @ {self.position.entry:.8g} | "
                    f"stop {self.position.stop:.8g} | alvo {self.position.target:.8g} | "
                    f"TEND_CONF={best['trend']}({best['trend_quality']:.1f}) | S={best['support']:.8g} R={best['resistance']:.8g} | TF=15M"
                )
        else:
            hot=', '.join(f"{x['symbol']} {x['gate']}/5" for x in near[:5]) or 'nenhum'
            self.add_log(f'15M fechado | sem entrada | Top25 analisado={len(self.focus)} | quase-sinais: {hot}')

    def run_loop(self):
        if not self.universe:
            self.discover_universe()
        while not self.stop_event.is_set():
            try:
                now=time.time()
                if now-self.last_radar_at >= RADAR_REFRESH_SECONDS:
                    self.refresh_radar_batch()

                # Independent observer: records hypothetical expiry outcomes only.
                self.duration_clock.update()

                with self.lock:
                    if self.position:
                        self._check_open_position_market()

                # Decision runs once for each newly closed 15M slot, not every 5M/noisy candle.
                utc_now=pd.Timestamp.now(tz='UTC')
                slot=utc_now.floor('15min')
                slot_id=str(slot)
                # Wait a few seconds after the boundary so exchanges finalize OHLCV.
                if utc_now.minute % 15 == 0 and utc_now.second < 8:
                    pass
                elif slot_id != self.last_decision_slot:
                    with self.lock:
                        self.run_decision_cycle()
                        self.last_decision_slot=slot_id
                        self.decision_count += 1
                        self.last_update=datetime.now(timezone.utc).isoformat(); self.last_error=None; self.save_state()

            except Exception as e:
                with self.lock: self.last_error=repr(e)
                self.add_log(f'Erro: {repr(e)}')
            self.stop_event.wait(POLL_SECONDS)
        with self.lock: self.running=False