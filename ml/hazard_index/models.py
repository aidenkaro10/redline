"""
Three models for "will hazard H reach level K at this site tomorrow?" (a yes/no
question, answered with a probability). All plain numpy + scipy, no new libraries.

  Climatology      how often it happened in that month at that site (or the
                   nearest training site, for a site the model never saw)
  Logistic         logistic regression on the features (L2-regularized)
  Boosted trees    small gradient-boosted decision trees (depth 3), the strong one

Most site-days are zero, so training up-weights the rare "yes" days (class
weights). Up-weighting inflates the probabilities, so afterwards we undo it
exactly: subtract log(weight) from the model's log-odds ("prior correction").
That keeps probabilities honest, which the 0-10 index needs.

Every model has the same two methods:
    fit(X, y, meta)     X: features (n, d), y: 0/1 (n,), meta: DataFrame with site_id, date, lat, lon
    predict(X, meta)    -> probabilities (n,)
"""

import json

import numpy as np
import pandas as pd
from scipy.optimize import minimize

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
# Shared feature prep: fill gaps with the training median, scale to mean 0 / sd 1
# ---------------------------------------------------------------------------
class _Prep:
    def fit(self, X):
        self.med = np.nanmedian(X, axis=0)
        self.med = np.where(np.isnan(self.med), 0.0, self.med)
        Xf = np.where(np.isnan(X), self.med, X)
        self.mu, self.sd = Xf.mean(0), Xf.std(0)
        self.sd[self.sd < 1e-9] = 1.0
        return self

    def __call__(self, X):
        return (np.where(np.isnan(X), self.med, X) - self.mu) / self.sd


# ---------------------------------------------------------------------------
# Logistic regression
# ---------------------------------------------------------------------------
class Logistic:
    name = "logistic"

    def __init__(self, l2: float = 1.0):
        self.l2 = l2

    def fit(self, X, y, meta=None):
        self.prep = _Prep().fit(X)
        Z = np.hstack([self.prep(X), np.ones((len(X), 1))])
        self.cw = class_weight(y)
        w = np.where(y == 1, self.cw, 1.0)

        def loss(beta):
            z = Z @ beta
            # weighted log-loss, written stably: log(1 + e^z) - y z
            ll = np.sum(w * (np.logaddexp(0, z) - y * z))
            reg = 0.5 * self.l2 * np.sum(beta[:-1] ** 2)
            grad = Z.T @ (w * (sigmoid(z) - y)) + self.l2 * np.r_[beta[:-1], 0.0]
            return ll + reg, grad

        self.beta = minimize(loss, np.zeros(Z.shape[1]), jac=True, method="L-BFGS-B").x
        return self

    def predict(self, X, meta=None):
        Z = np.hstack([self.prep(X), np.ones((len(X), 1))])
        return sigmoid(Z @ self.beta - np.log(self.cw))      # undo the class weight


# ---------------------------------------------------------------------------
# Gradient-boosted trees (histogram style, like LightGBM but tiny)
# ---------------------------------------------------------------------------
class BoostedTrees:
    name = "boosted_trees"

    def __init__(self, n_trees=300, depth=3, lr=0.05, n_bins=32, min_hess=5.0,
                 l2=5.0, subsample=0.8, seed=0):
        self.n_trees, self.depth, self.lr, self.n_bins = n_trees, depth, lr, n_bins
        self.min_hess, self.l2, self.subsample, self.seed = min_hess, l2, subsample, seed

    # --- turn each feature into small integers (bins) once ---
    def _fit_bins(self, X):
        qs = np.linspace(0, 1, self.n_bins + 1)[1:-1]
        self.edges = [np.unique(np.nanquantile(col, qs)) if np.isfinite(col).any() else np.array([])
                      for col in X.T]

    def _bin(self, X):
        B = np.empty(X.shape, dtype=np.int16)
        for j, e in enumerate(self.edges):
            col = X[:, j]
            B[:, j] = np.where(np.isnan(col), 0, np.searchsorted(e, col, side="right") + 1)  # bin 0 = missing
        return B

    def _grow(self, B, g, h, rows):
        """Grow one tree greedily. Returns a list of nodes (dicts)."""
        nodes = [dict(rows=rows, depth=0)]
        i = 0
        while i < len(nodes):
            node = nodes[i]
            r = node.pop("rows")
            G, Hs = g[r].sum(), h[r].sum()
            node["value"] = -G / (Hs + self.l2)
            if node["depth"] < self.depth and Hs >= 2 * self.min_hess:
                best = (0.0, None, None)
                parent = G * G / (Hs + self.l2)
                for j in range(B.shape[1]):
                    nb = len(self.edges[j]) + 2
                    gb = np.bincount(B[r, j], weights=g[r], minlength=nb)
                    hb = np.bincount(B[r, j], weights=h[r], minlength=nb)
                    gl, hl = np.cumsum(gb)[:-1], np.cumsum(hb)[:-1]       # left = bins <= t
                    gr, hr = G - gl, Hs - hl
                    ok = (hl >= self.min_hess) & (hr >= self.min_hess)
                    if not ok.any():
                        continue
                    gain = np.where(ok, gl * gl / (hl + self.l2) + gr * gr / (hr + self.l2) - parent, -np.inf)
                    t = int(np.argmax(gain))
                    if gain[t] > best[0]:
                        best = (float(gain[t]), j, t)
                if best[1] is not None:
                    j, t = best[1], best[2]
                    left = r[B[r, j] <= t]
                    right = r[B[r, j] > t]
                    node.update(feature=j, threshold=t, left=len(nodes), right=len(nodes) + 1, gain=best[0])
                    nodes.append(dict(rows=left, depth=node["depth"] + 1))
                    nodes.append(dict(rows=right, depth=node["depth"] + 1))
            i += 1
        return nodes

    @staticmethod
    def _apply(tree, B):
        out = np.empty(len(B))
        idx = np.zeros(len(B), dtype=np.int64)          # every row starts at the root
        done = np.zeros(len(B), dtype=bool)
        while not done.all():
            for k in np.unique(idx[~done]):
                node = tree[k]
                at = (idx == k) & ~done
                if "feature" not in node:
                    out[at] = node["value"]
                    done[at] = True
                else:
                    go_left = B[at, node["feature"]] <= node["threshold"]
                    rows = np.flatnonzero(at)
                    idx[rows[go_left]] = node["left"]
                    idx[rows[~go_left]] = node["right"]
        return out

    def fit(self, X, y, meta=None, X_val=None, y_val=None):
        rng = np.random.default_rng(self.seed)
        self._fit_bins(X)
        B = self._bin(X)
        self.cw = class_weight(y)
        w = np.where(y == 1, self.cw, 1.0)
        p0 = np.clip(np.average(y, weights=w), 1e-4, 1 - 1e-4)
        self.base = float(np.log(p0 / (1 - p0)))
        F = np.full(len(y), self.base)
        self.trees, self.importance = [], np.zeros(X.shape[1])
        Bv = self._bin(X_val) if X_val is not None else None
        Fv = np.full(len(X_val), self.base) if X_val is not None else None
        best_loss, best_n = np.inf, 0
        for k in range(self.n_trees):
            p = sigmoid(F)
            g, h = w * (p - y), w * p * (1 - p)
            rows = np.flatnonzero(rng.random(len(y)) < self.subsample)
            tree = self._grow(B, g, h, rows)
            for node in tree:
                node["value"] *= self.lr
                if "feature" in node:
                    self.importance[node["feature"]] += node["gain"]
            self.trees.append(tree)
            F += self._apply(tree, B)
            if Bv is not None:                         # early stopping on the validation year
                Fv += self._apply(tree, Bv)
                pv = sigmoid(Fv - np.log(self.cw))
                loss = -np.mean(y_val * np.log(pv + 1e-12) + (1 - y_val) * np.log(1 - pv + 1e-12))
                if loss < best_loss - 1e-6:
                    best_loss, best_n = loss, k + 1
                elif k + 1 - best_n >= 40:
                    break
        if Bv is not None:
            self.trees = self.trees[:max(best_n, 1)]
        return self

    def raw_score(self, X):
        B = self._bin(X)
        return self.base + sum(self._apply(t, B) for t in self.trees)

    def predict(self, X, meta=None):
        return sigmoid(self.raw_score(X) - np.log(self.cw))

    def contributions(self, x: np.ndarray) -> np.ndarray:
        """
        Why did ONE row score what it did? Walk each tree; every split hands the
        change in node value to the feature it split on (the "Saabas" method).
        Returns (per-feature numbers, bias). Positive = pushed risk up.
        raw_score = base + bias + sum(per-feature numbers).
        """
        B = self._bin(x[None, :])[0]
        contrib = np.zeros(len(x))
        bias = 0.0
        for tree in self.trees:
            k = 0
            bias += tree[0]["value"]
            while "feature" in tree[k]:
                node = tree[k]
                nxt = node["left"] if B[node["feature"]] <= node["threshold"] else node["right"]
                contrib[node["feature"]] += tree[nxt]["value"] - node["value"]
                k = nxt
        return contrib, bias

    # --- save / load as plain JSON (small, readable, no pickle) ---
    def to_dict(self):
        return dict(base=self.base, cw=self.cw, edges=[e.tolist() for e in self.edges],
                    trees=[[{k: v for k, v in n.items() if k != "depth"} for n in t] for t in self.trees])

    @classmethod
    def from_dict(cls, d):
        m = cls()
        m.base, m.cw = d["base"], d["cw"]
        m.edges = [np.array(e) for e in d["edges"]]
        m.trees = d["trees"]
        return m


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


def save_json(obj, path):
    path.write_text(json.dumps(obj))
