import os
import json
import pandas as pd

# Relógio SHADOW de duração — observador independente.
# Não abre/fecha operações e não altera stop, alvo ou sinais.
DURATION_SHADOW_FILE = os.getenv("DURATION_SHADOW_FILE", "duration_shadow.json")
DURATION_RESULTS_FILE = os.getenv("DURATION_RESULTS_FILE", "duration_shadow.csv")
DURATION_HORIZONS = (5, 10, 15, 20, 30, 45, 60)


class DurationShadowClock:
    """
    Requer duas funções do bot hospedeiro:
      fetch_df(symbol, timeframe, limit) -> DataFrame com colunas 'dt' e 'close'
      add_log(message) -> registra uma linha no log
    """

    def __init__(self, fetch_df, add_log):
        self.fetch_df = fetch_df
        self.add_log = add_log
        self.items = self._load()

    def _load(self):
        if not os.path.exists(DURATION_SHADOW_FILE):
            return []
        try:
            with open(DURATION_SHADOW_FILE, "r", encoding="utf-8") as f:
                rows = json.load(f)
            return rows if isinstance(rows, list) else []
        except Exception:
            return []

    def _save(self):
        try:
            with open(DURATION_SHADOW_FILE, "w", encoding="utf-8") as f:
                json.dump(self.items, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.add_log(f"DURATION SHADOW | erro ao salvar: {repr(e)}")

    @staticmethod
    def _result(side, entry, exit_price):
        # Usa precisão completa do feed; não arredonda para decidir.
        if exit_price == entry:
            return "TIE"
        if side == "long":
            return "WIN" if exit_price > entry else "LOSS"
        return "WIN" if exit_price < entry else "LOSS"

    def register(self, symbol, side, entry, opened_at, reason="", context=None):
        context = context or {}
        item = {
            "id": f"{symbol}|{opened_at}",
            "symbol": symbol,
            "side": side,
            "entry": float(entry),
            "opened_at": str(opened_at),
            "reason": reason,
            "context": context,
            "pending": list(DURATION_HORIZONS),
            "results": {},
        }
        self.items.append(item)
        self._save()
        horizons = "/".join(str(x) for x in DURATION_HORIZONS)
        self.add_log(
            f"DURATION SHADOW | registrado {symbol} | observar {horizons}M"
        )

    def update(self):
        if not self.items:
            return

        now = pd.Timestamp.now(tz="UTC")
        changed = False

        for item in self.items:
            pending = list(item.get("pending", []))
            if not pending:
                continue

            opened = pd.Timestamp(item["opened_at"])
            if opened.tzinfo is None:
                opened = opened.tz_localize("UTC")
            else:
                opened = opened.tz_convert("UTC")

            due = [
                m for m in pending
                if now >= opened + pd.Timedelta(minutes=int(m))
            ]
            if not due:
                continue

            try:
                # 1M é somente a régua de medição da duração.
                # Não participa da decisão de entrada.
                df = self.fetch_df(item["symbol"], "1m", 120)

                for m in sorted(due):
                    target = opened + pd.Timedelta(minutes=int(m))
                    eligible = df[df["dt"] >= target.floor("min")]
                    if eligible.empty:
                        continue

                    candle = eligible.iloc[0]
                    exit_price = float(candle["close"])
                    entry = float(item["entry"])
                    result = self._result(item["side"], entry, exit_price)

                    delta = (
                        exit_price - entry
                        if item["side"] == "long"
                        else entry - exit_price
                    )
                    delta_pct = (delta / entry * 100.0) if entry else 0.0

                    row = {
                        "shadow_id": item["id"],
                        "symbol": item["symbol"],
                        "side": item["side"],
                        "opened_at": item["opened_at"],
                        "horizon_min": int(m),
                        "entry": entry,
                        "observed_at": str(candle["dt"]),
                        "exit_price": exit_price,
                        "result": result,
                        "delta": delta,
                        "delta_pct": delta_pct,
                        "reason": item.get("reason", ""),
                        "context_json": json.dumps(
                            item.get("context", {}),
                            ensure_ascii=False,
                        ),
                    }

                    pd.DataFrame([row]).to_csv(
                        DURATION_RESULTS_FILE,
                        mode="a",
                        header=not os.path.exists(DURATION_RESULTS_FILE),
                        index=False,
                    )

                    item.setdefault("results", {})[str(m)] = row
                    item["pending"].remove(m)
                    changed = True

                    self.add_log(
                        f"DURATION SHADOW | {item['symbol']} +{m}M = {result} | "
                        f"{entry:.8g}->{exit_price:.8g} | delta={delta_pct:+.4f}%"
                    )

            except Exception as e:
                self.add_log(
                    f"DURATION SHADOW | {item.get('symbol')} erro: {repr(e)}"
                )

        if changed:
            # CSV guarda o histórico completo; JSON mantém observações recentes/ativas.
            if len(self.items) > 200:
                completed = [x for x in self.items if not x.get("pending")]
                active = [x for x in self.items if x.get("pending")]
                self.items = completed[-100:] + active
            self._save()
