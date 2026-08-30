import os
import json
import pandas as pd

LAB_HISTORY_FILE = os.getenv("LAB_HISTORY_FILE", "lab_history.csv")
HORIZONS = (5, 10, 15, 20, 30, 45, 60)


class LabHistory:
    """Arquivo acumulado de telemetria. Não interfere em sinais nem operações."""

    def __init__(self, add_log):
        self.add_log = add_log

    @staticmethod
    def _context_value(context, key, default=None):
        value = context.get(key, default)
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return value

    def _read(self):
        if not os.path.exists(LAB_HISTORY_FILE):
            return pd.DataFrame()
        try:
            return pd.read_csv(LAB_HISTORY_FILE, dtype={"shadow_id": str})
        except Exception as e:
            self.add_log(f"LAB HISTORY | erro ao ler: {repr(e)}")
            return pd.DataFrame()

    def _write(self, df):
        try:
            df.to_csv(LAB_HISTORY_FILE, index=False)
        except Exception as e:
            self.add_log(f"LAB HISTORY | erro ao salvar: {repr(e)}")

    def register_sample(self, item):
        """Cria exatamente uma linha por amostra; chamadas repetidas são ignoradas."""
        df = self._read()
        item_id = str(item.get("id", ""))
        if not df.empty and "shadow_id" in df.columns and (df["shadow_id"].astype(str) == item_id).any():
            return False

        context = item.get("context", {}) or {}
        row = {
            "shadow_id": item_id,
            "sample_type": item.get("sample_type", "shadow"),
            "opened_at": item.get("opened_at", ""),
            "symbol": item.get("symbol", ""),
            "side": item.get("side", ""),
            "entry_raw": item.get("entry"),
            "reason": item.get("reason", ""),
            "missing": ",".join(context.get("missing", [])),
            "direction_source": context.get("direction_source", ""),
            "trend": context.get("trend", ""),
            "trend_quality": context.get("trend_quality"),
            "direction_1h": context.get("direction_1h", ""),
            "direction_15m": context.get("direction_15m", ""),
            "support": context.get("support"),
            "resistance": context.get("resistance"),
            "level_dist_atr": context.get("level_dist_atr"),
            "atr": context.get("atr"),
            "radar_score": context.get("radar_score"),
            "decision_timeframe": context.get("decision_timeframe", ""),
            "context_json": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        }
        for m in HORIZONS:
            row[f"result_{m}m"] = ""
            row[f"exit_{m}m_raw"] = ""
            row[f"delta_{m}m_pct"] = ""
            row[f"observed_{m}m_at"] = ""

        out = pd.concat([df, pd.DataFrame([row])], ignore_index=True, sort=False)
        self._write(out)
        return True

    def update_horizon(self, item, result_row):
        """Atualiza a linha da amostra com o resultado de uma duração."""
        df = self._read()
        if df.empty or "shadow_id" not in df.columns:
            self.register_sample(item)
            df = self._read()
        item_id = str(item.get("id", ""))
        mask = df["shadow_id"].astype(str) == item_id
        if not mask.any():
            self.register_sample(item)
            df = self._read()
            mask = df["shadow_id"].astype(str) == item_id
            if not mask.any():
                return False

        m = int(result_row["horizon_min"])
        values = {
            f"result_{m}m": result_row.get("result", ""),
            f"exit_{m}m_raw": result_row.get("exit_price", ""),
            f"delta_{m}m_pct": result_row.get("delta_pct", ""),
            f"observed_{m}m_at": result_row.get("observed_at", ""),
        }
        for col, value in values.items():
            if col not in df.columns:
                df[col] = ""
            df.loc[mask, col] = value
        self._write(df)
        return True
