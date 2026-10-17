"""
Three models for "will hazard H reach level K at this site tomorrow?" (a yes/no
question, answered with a probability). Built on scikit-learn.

  Climatology      how often it happened in that month at that site (or the
                   nearest training site, for a site the model never saw)
  Logistic         logistic regression on the features (L2-regularized)
  Boosted trees    small gradient-boosted decision trees (depth 3)
  Blend            average of the two (the one we ship, see evaluate.py)

Most site-days are zero, so training up-weights the rare "yes" days (class
weights). Up-weighting inflates the probabilities, so afterwards we undo it
exactly: subtract log(weight) from the model's log-odds ("prior correction").
That keeps probabilities honest, which the 0-10 index needs.

Every model has the same two methods:
    fit(X, y, meta)     X: features (n, d), y: 0/1 (n,), meta: DataFrame with site_id, date, lat, lon
    predict(X, meta)    -> probabilities (n,)
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml.hazard_index.sites import distance_km

MAX_CLASS_WEIGHT = 25.0       # never up-weight a "yes" day more than this


def class_weight(y: np.ndarray) -> float:
    """Weight for "yes" days so yes and no count about equally (capped)."""
    pos = max(y.sum(), 1)
    return float(min((len(y) - pos) / pos, MAX_CLASS_WEIGHT)) if pos < len(y) / 2 else 1.0


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


# ---------------------------------------------------------------------------
# Climatology
# ---------------------------------------------------------------------------
class Climatology:
    """P(yes) by site and month, smoothed toward the all-site rate for that month."""
    name = "climatology"
    PRIOR_DAYS = 30.0     # smoothing: a site's month counts like this many extra pooled days

    def fit(self, X, y, meta):
        m = meta.assign(y=y, month=[d.month for d in meta["date"]])
        self.month_rate = m.groupby("month")["y"].mean().to_dict()
        g = m.groupby(["site_id", "month"])["y"].agg(["sum", "count"])
        self.site_month = {}
        for (sid, mon), r in g.iterrows():
            prior = self.month_rate[mon]
            self.site_month[(sid, mon)] = (r["sum"] + self.PRIOR_DAYS * prior) / (r["count"] + self.PRIOR_DAYS)
        self.site_xy = meta.groupby("site_id")[["lat", "lon"]].first()
        self.overall = float(np.mean(y))
        return self

    def _nearest_site(self, lat, lon):
        d = [distance_km(lat, lon, r.lat, r.lon) for r in self.site_xy.itertuples()]
        return self.site_xy.index[int(np.argmin(d))]

    def predict(self, X, meta):
        out = np.empty(len(meta))
        nearest = {}
        for i, r in enumerate(meta.itertuples()):
            sid, mon = r.site_id, r.date.month
            if sid not in self.site_xy.index:                  # unseen site: borrow the nearest one
                if sid not in nearest:
                    nearest[sid] = self._nearest_site(r.lat, r.lon)
                sid = nearest[sid]
            out[i] = self.site_month.get((sid, mon), self.month_rate.get(mon, self.overall))
        return out


# ---------------------------------------------------------------------------
# Logistic regression (scikit-learn)
# ---------------------------------------------------------------------------
class Logistic:
    """Fill gaps with the training median, scale features, then L2 logistic regression."""
    name = "logistic"

    def __init__(self, C: float = 1.0):
        self.C = C

    def fit(self, X, y, meta=None):
        self.cw = class_weight(y)
        self.model = make_pipeline(
            SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(),
            LogisticRegression(C=self.C, class_weight={0: 1.0, 1: self.cw}, max_iter=2000))
        self.model.fit(X, y)
        return self

    def raw_score(self, X):
        return self.model.decision_function(X)

    def predict(self, X, meta=None):
        return sigmoid(self.raw_score(X) - np.log(self.cw))      # undo the class weight


# ---------------------------------------------------------------------------
# Gradient-boosted trees (scikit-learn's HistGradientBoostingClassifier)
# ---------------------------------------------------------------------------
class BoostedTrees:
    """
    Small boosted trees. Handles missing values itself. Stops adding trees when
    the validation set (passed in, e.g. the last training year) stops improving.
    """
    name = "boosted_trees"

    def __init__(self, max_iter=400, max_depth=3, learning_rate=0.05, l2=1.0, min_samples_leaf=40, seed=0):
        self.params = dict(max_iter=max_iter, max_depth=max_depth, learning_rate=learning_rate,
                           l2_regularization=l2, min_samples_leaf=min_samples_leaf, random_state=seed)

    def fit(self, X, y, meta=None, X_val=None, y_val=None):
        self.cw = class_weight(y)
        cw = {0: 1.0, 1: self.cw}
        if X_val is not None:
            # pick the number of trees on the validation rows, then refit on everything
            probe = HistGradientBoostingClassifier(class_weight=cw, early_stopping=False, **self.params)
            probe.fit(X, y)
            best, best_n = np.inf, 1
            for n, z in enumerate(probe.staged_decision_function(X_val), start=1):
                p = sigmoid(np.ravel(z) - np.log(self.cw))
                loss = -np.mean(y_val * np.log(p + 1e-12) + (1 - y_val) * np.log(1 - p + 1e-12))
                if loss < best - 1e-6:
                    best, best_n = loss, n
            self.params["max_iter"] = best_n
        self.model = HistGradientBoostingClassifier(class_weight=cw, early_stopping=False, **self.params)
        self.model.fit(X, y)
        self.n_trees = self.model.n_iter_
        return self

    def raw_score(self, X):
        return self.model.decision_function(X)

    def predict(self, X, meta=None):
        return sigmoid(self.raw_score(X) - np.log(self.cw))      # undo the class weight


class Blend:
    """Average of logistic regression and boosted trees (in probability).
    The trees catch thresholds and interactions; the logistic part keeps going
    up when a hot year goes past anything in the training data."""
    name = "blend"

    def __init__(self, logistic: Logistic, trees: BoostedTrees):
        self.logistic, self.trees = logistic, trees

    def predict(self, X, meta=None):
        return 0.5 * self.logistic.predict(X) + 0.5 * self.trees.predict(X)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def pr_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Average precision: area under the precision-recall curve. Random = base rate."""
    y = np.asarray(y)
    if y.sum() == 0:
        return float("nan")
    order = np.argsort(-p, kind="mergesort")
    y = y[order]
    tp = np.cumsum(y)
    precision = tp / np.arange(1, len(y) + 1)
    return float(np.sum(precision * y) / y.sum())


def brier(y, p) -> float:
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def calibration_table(y, p, n_bins: int = 10) -> pd.DataFrame:
    """Predicted vs. actual rate in bins of predicted probability."""
    edges = np.quantile(p, np.linspace(0, 1, n_bins + 1))
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, n_bins - 1)
    rows = [dict(bin=b, n=int((idx == b).sum()), predicted=float(p[idx == b].mean()),
                 actual=float(np.asarray(y)[idx == b].mean())) for b in range(n_bins) if (idx == b).any()]
    return pd.DataFrame(rows)


def ece(y, p, n_bins: int = 10) -> float:
    """Expected calibration error: average |predicted - actual| over the bins, weighted by size."""
    t = calibration_table(y, p, n_bins)
    return float(np.sum(t["n"] * np.abs(t["predicted"] - t["actual"])) / t["n"].sum())

