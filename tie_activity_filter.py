import os
from collections import defaultdict

import pandas as pd


DURATION_RESULTS_FILE = os.getenv("DURATION_RESULTS_FILE", "duration_shadow.csv")
TIE_FILTER_ENABLED = os.getenv("TIE_FILTER_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
TIE_LOOKBACK_SAMPLES = int(os.getenv("TIE_LOOKBACK_SAMPLES", "6"))
TIE_MIN_SAMPLES = int(os.getenv("TIE_MIN_SAMPLES", "3"))
TIE_SAMPLE_MIN_OBS = int(os.getenv("TIE_SAMPLE_MIN_OBS", "4"))
TIE_SAMPLE_RATIO = float(os.getenv("TIE_SAMPLE_RATIO", "0.75"))
TIE_STAGNANT_RATIO_START = float(os.getenv("TIE_STAGNANT_RATIO_START", "0.67"))
TIE_PENALTY_MAX = float(os.getenv("TIE_PENALTY_MAX", "6.0"))


class TieActivityFilter:
    """
    Penalização leve de ranking para pares persistentemente sem movimento.

    Segurança:
    - não altera trigger, 4/5, 5/5, PAPER, stop ou alvo;
    - um TIE isolado nunca penaliza;
    - mede amostras independentes (shadow_id), não conta 7 horizontes como 7 sinais;
    - só começa após várias amostras recentes com observações suficientes.
    """

    def __init__(self, results_file=DURATION_RESULTS_FILE):
        self.results_file = results_file
        self._mtime = None
        self._penalties = {}
        self._meta = {}

    def _reload_if_needed(self):
        if not TIE_FILTER_ENABLED or not os.path.exists(self.results_file):
            self._penalties = {}
            self._meta = {}
            return

        try:
            mtime = os.path.getmtime(self.results_file)
            if self._mtime == mtime:
                return
            self._mtime = mtime

            df = pd.read_csv(self.results_file)
            required = {"shadow_id", "symbol", "result", "horizon_min"}
            if df.empty or not required.issubset(df.columns):
                self._penalties = {}
                self._meta = {}
                return

            df = df[df["result"].isin(["WIN", "LOSS", "TIE"])].copy()
            if df.empty:
                self._penalties = {}
                self._meta = {}
                return

            # One summary per independent signal/sample. This prevents one sample's
            # 5/10/15/20/30/45/60M outcomes from being treated as seven signals.
            sample_rows = []
            for (symbol, shadow_id), g in df.groupby(["symbol", "shadow_id"], sort=False):
                g = g.drop_duplicates(subset=["horizon_min"], keep="last")
                observed = int(len(g))
                if observed < TIE_SAMPLE_MIN_OBS:
                    continue
                ties = int((g["result"] == "TIE").sum())
                tie_ratio = ties / observed if observed else 0.0
                sample_rows.append({
                    "symbol": symbol,
                    "shadow_id": shadow_id,
                    "observed": observed,
                    "ties": ties,
                    "tie_ratio": tie_ratio,
                    "stagnant": tie_ratio >= TIE_SAMPLE_RATIO,
                    "order": int(g.index.max()),
                })

            by_symbol = defaultdict(list)
            for row in sample_rows:
                by_symbol[row["symbol"]].append(row)

            penalties = {}
            meta = {}
            for symbol, rows in by_symbol.items():
                rows = sorted(rows, key=lambda x: x["order"], reverse=True)[:max(1, TIE_LOOKBACK_SAMPLES)]
                n = len(rows)
                stagnant = sum(1 for x in rows if x["stagnant"])
                stagnant_ratio = stagnant / n if n else 0.0

                penalty = 0.0
                # Require repeated evidence. With defaults: at least 3 recent samples,
                # of which at least ~2/3 are stagnant.
                if n >= TIE_MIN_SAMPLES and stagnant_ratio >= TIE_STAGNANT_RATIO_START:
                    span = max(1e-9, 1.0 - TIE_STAGNANT_RATIO_START)
                    strength = min(1.0, max(0.0, (stagnant_ratio - TIE_STAGNANT_RATIO_START) / span))
                    # Minimum useful penalty is half max once threshold is proven;
                    # it grows gradually only if stagnation persists.
                    penalty = (0.5 + 0.5 * strength) * TIE_PENALTY_MAX

                penalties[symbol] = round(penalty, 2)
                meta[symbol] = {
                    "recent_samples": n,
                    "stagnant_samples": stagnant,
                    "stagnant_ratio": round(stagnant_ratio, 3),
                    "penalty": round(penalty, 2),
                }

            self._penalties = penalties
            self._meta = meta
        except Exception:
            # Fail-open: ranking remains exactly as before if laboratory data is unavailable.
            self._penalties = {}
            self._meta = {}

    def penalty(self, symbol):
        self._reload_if_needed()
        return float(self._penalties.get(symbol, 0.0))

    def meta(self, symbol):
        self._reload_if_needed()
        return dict(self._meta.get(symbol, {}))
