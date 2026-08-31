import os
import json
import pandas as pd

DURATION_SHADOW_FILE = os.getenv("DURATION_SHADOW_FILE", "duration_shadow.json")
DURATION_RESULTS_FILE = os.getenv("DURATION_RESULTS_FILE", "duration_shadow.csv")
DURATION_HORIZONS = (5, 10, 15, 20, 30, 45, 60)


class DurationShadowClock:
    """Observador independente: não abre/fecha operações e não altera sinais."""

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
        if exit_price == entry:
            return "TIE"
        if side == "long":
            return "WIN" if exit_price > entry else "LOSS"
        return "WIN" if exit_price < entry else "LOSS"

    def register(self, symbol, side, entry, opened_at, reason="", context=None, sample_id=None):
        context = context or {}
        item_id = sample_id or f"{symbol}|{opened_at}"
        if any(x.get("id") == item_id for x in self.items):
            return False

        sample_type = context.get("sample_type", "shadow")
        missing = context.get("missing", [])
        self.items.append({
            "id": item_id, "sample_type": sample_type, "symbol": symbol, "side": side,
            "entry": float(entry), "opened_at": str(opened_at), "reason": reason,
            "context": context, "pending": list(DURATION_HORIZONS), "results": {},
        })
        self._save()
        missing_txt = ",".join(missing) if missing else "nenhum"
        horizons = "/".join(str(x) for x in DURATION_HORIZONS)
        self.add_log(
            f"DURATION SHADOW | registrado {symbol} | tipo={sample_type} | "
            f"faltando={missing_txt} | observar {horizons}M"
        )
        return True

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
            opened = opened.tz_localize("UTC") if opened.tzinfo is None else opened.tz_convert("UTC")
            due = [m for m in pending if now >= opened + pd.Timedelta(minutes=int(m))]
            if not due:
                continue
            try:
                df = self.fetch_df(item["symbol"], "1m", 120).copy()
                df["dt"] = pd.to_datetime(df["dt"], utc=True)
                # Só velas 1M fechadas.
                df = df[df["dt"] < now.floor("min")]

                for m in sorted(due):
                    target = opened + pd.Timedelta(minutes=int(m))
                    eligible = df[df["dt"] >= target.floor("min")]
                    if eligible.empty:
                        continue
                    candle = eligible.iloc[0]
                    exit_price = float(candle["close"])
                    entry = float(item["entry"])
                    result = self._result(item["side"], entry, exit_price)
                    delta = exit_price-entry if item["side"] == "long" else entry-exit_price
                    delta_pct = (delta/entry*100.0) if entry else 0.0

                    row = {
                        "shadow_id": item["id"], "sample_type": item.get("sample_type", "shadow"),
                        "symbol": item["symbol"], "side": item["side"], "opened_at": item["opened_at"],
                        "horizon_min": int(m), "entry": entry, "observed_at": str(candle["dt"]),
                        "exit_price": exit_price, "result": result, "delta": delta,
                        "delta_pct": delta_pct, "reason": item.get("reason", ""),
                        "missing": ",".join(item.get("context", {}).get("missing", [])),
                        "context_json": json.dumps(item.get("context", {}), ensure_ascii=False),
                    }
                    pd.DataFrame([row]).to_csv(
                        DURATION_RESULTS_FILE, mode="a",
                        header=not os.path.exists(DURATION_RESULTS_FILE), index=False
                    )
                    item.setdefault("results", {})[str(m)] = row
                    item["pending"].remove(m)
                    changed = True
                    self.add_log(
                        f"DURATION SHADOW | {item['symbol']} | tipo={item.get('sample_type','shadow')} "
                        f"+{m}M = {result} | {entry:.17g}->{exit_price:.17g} | delta={delta_pct:+.8f}%"
                    )
            except Exception as e:
                self.add_log(f"DURATION SHADOW | {item.get('symbol')} erro: {repr(e)}")

        if changed:
            if len(self.items) > 200:
                completed = [x for x in self.items if not x.get("pending")]
                active = [x for x in self.items if x.get("pending")]
                self.items = completed[-100:] + active
            self._save()

    def reset(self):
        self.items = []
        for path in (DURATION_SHADOW_FILE, DURATION_RESULTS_FILE):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                self.add_log(f"DURATION SHADOW | erro no reset de {path}: {repr(e)}")
