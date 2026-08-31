import time
import pandas as pd


class PrecisionProbe:
    """Diagnóstico somente-leitura da precisão Kraken/CCXT.

    Não altera sinais, entradas, stops, targets, saldo ou resultados SHADOW.
    Apenas compara o OHLC bruto da Kraken com o OHLC já parseado pelo CCXT
    e, como referência, mostra ticker/trade recente quando disponível.
    """

    DEFAULT_SYMBOLS = ("BERA/USDT", "OBOL/USDT", "RLUSD/USDT", "USDPT/USDT", "KAS/USDT")

    def __init__(self, exchange, add_log, interval_seconds=900, symbols=None):
        self.exchange = exchange
        self.add_log = add_log
        self.interval_seconds = int(interval_seconds)
        self.symbols = tuple(symbols or self.DEFAULT_SYMBOLS)
        self.last_run = 0.0

    def update(self):
        now = time.time()
        if self.last_run and now - self.last_run < self.interval_seconds:
            return
        self.last_run = now
        for symbol in self.symbols:
            try:
                self._probe_symbol(symbol)
            except Exception as e:
                self.add_log(f"PRECISION PROBE | {symbol} | erro={repr(e)}")

    def _raw_ohlc(self, symbol):
        market = self.exchange.market(symbol)
        pair_id = market.get("id") or symbol.replace("/", "")

        method = None
        for name in ("public_get_ohlc", "publicGetOHLC", "publicGetOhlc"):
            candidate = getattr(self.exchange, name, None)
            if callable(candidate):
                method = candidate
                break
        if method is None:
            raise RuntimeError("método OHLC bruto da Kraken não encontrado no CCXT")

        response = method({"pair": pair_id, "interval": 1})
        result = response.get("result", {}) if isinstance(response, dict) else {}
        rows = None
        for key, value in result.items():
            if key == "last":
                continue
            if isinstance(value, list):
                rows = value
                break
        if not rows:
            raise RuntimeError("Kraken RAW OHLC sem candles")

        # Kraken inclui a vela corrente; usamos a penúltima como vela fechada.
        row = rows[-2] if len(rows) >= 2 else rows[-1]
        return {
            "ts": int(float(row[0])) * 1000,
            "open": str(row[1]),
            "high": str(row[2]),
            "low": str(row[3]),
            "close": str(row[4]),
        }

    def _parsed_ohlc(self, symbol, target_ts):
        rows = self.exchange.fetch_ohlcv(symbol, timeframe="1m", limit=10)
        if not rows:
            raise RuntimeError("CCXT OHLC sem candles")
        closed = rows[:-1] if len(rows) > 1 else rows
        exact = [r for r in closed if int(r[0]) == int(target_ts)]
        row = exact[-1] if exact else closed[-1]
        return {
            "ts": int(row[0]),
            "close": float(row[4]),
        }

    def _ticker_last(self, symbol):
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            value = ticker.get("last") if isinstance(ticker, dict) else None
            return None if value is None else float(value)
        except Exception:
            return None

    def _trade_last(self, symbol):
        try:
            trades = self.exchange.fetch_trades(symbol, limit=5)
            if not trades:
                return None
            value = trades[-1].get("price")
            return None if value is None else float(value)
        except Exception:
            return None

    def _probe_symbol(self, symbol):
        raw = self._raw_ohlc(symbol)
        parsed = self._parsed_ohlc(symbol, raw["ts"])
        ticker = self._ticker_last(symbol)
        trade = self._trade_last(symbol)
        dt = pd.to_datetime(raw["ts"], unit="ms", utc=True)

        # str(raw['close']) preserva exatamente as casas entregues pela Kraken.
        parsed_txt = format(parsed["close"], ".17g")
        ticker_txt = "n/a" if ticker is None else format(ticker, ".17g")
        trade_txt = "n/a" if trade is None else format(trade, ".17g")

        try:
            same_numeric = float(raw["close"]) == parsed["close"]
        except Exception:
            same_numeric = False

        self.add_log(
            f"PRECISION PROBE | {symbol} | candle={dt.strftime('%H:%M')} | "
            f"KRAKEN_RAW_CLOSE='{raw['close']}' | CCXT_CLOSE={parsed_txt} | "
            f"igual={same_numeric} | TICKER={ticker_txt} | TRADE={trade_txt}"
        )
