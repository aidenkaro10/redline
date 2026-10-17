"""
Scoring harness for the hazard index. Every model is scored next to climatology.

Splits (never random):
  time         all sites; train 2018-2022, test 2023-2024
  time+space   sites are grouped with their neighbors (within 60 km). Each group
               is held out in turn: train on the OTHER groups 2018-2022, test the
               held-out group 2023-2024. Neighbors never sit on both sides.

What we report per question ("hazard H reaches level K tomorrow"):
  base rate, PR-AUC (random guessing scores the base rate), Brier score,
  calibration error (ECE), and a 95% interval for (model PR-AUC - climatology
  PR-AUC) from resampling test WEEKS (days in a week, and sites on one day, are
  not independent, so we resample whole weeks).

Flags:
  "TOO GOOD?"   PR-AUC above 0.9, or one feature carrying most of the model.
  "NO SIGNAL"   fewer than MIN_TEST_POSITIVES yes-days in the test years.

Run:  python -m ml.hazard_index.evaluate              (needs labels + features built)
      python -m ml.hazard_index.evaluate --quick      (fewer trees, faster)
"""

import argparse
import json

import numpy as np
import pandas as pd

from ml.hazard_index import fetch
from ml.hazard_index import hazards as H
from ml.hazard_index import models as M
from ml.hazard_index.features import FEATURES, load_features
from ml.hazard_index.labels import load_labels
from ml.hazard_index.sites import load_sites, neighbor_groups

TRAIN_YEARS = (2018, 2019, 2020, 2021, 2022)
TEST_YEARS = (2023, 2024)
VAL_YEAR = 2022              # the boosted trees pick their size on this year, then refit on all train years
LEVELS = (H.ADVISORY, H.WARNING, H.DAMAGE)
TARGETS = list(H.HAZARDS) + ["overall"]
MIN_TEST_POSITIVES = 20
TOO_GOOD_PR_AUC = 0.9
TOO_GOOD_FEATURE_SHARE = 0.6
N_BOOT = 500


def load_dataset(sites=None) -> pd.DataFrame:
    """Features + labels, one row per site per day, with site coordinates."""
    sites = sites or load_sites()
    feats, labels = load_features(), load_labels()
    df = feats.merge(labels, on=["site_id", "date"], how="inner")
    xy = pd.DataFrame([dict(site_id=s.site_id, lat=s.lat, lon=s.lon) for s in sites])
    df = df.merge(xy, on="site_id", how="inner")
    df["year"] = [d.year for d in df["date"]]
    return df


def target(df: pd.DataFrame, hazard: str, level: int) -> np.ndarray:
    return (df[hazard].to_numpy() >= level).astype(float)


def fit_boosted(train: pd.DataFrame, y: np.ndarray, features, n_trees: int, val_year: int = VAL_YEAR):
    """Pick the number of trees on val_year (trained without it), then refit on all training years."""
    X = train[features].to_numpy(float)
    val = (train["year"] == val_year).to_numpy()
    n = n_trees // 2
    if val.any() and (~val).any() and y[val].sum() >= 5 and y[~val].sum() >= 5:
        probe = M.BoostedTrees(max_iter=n_trees).fit(X[~val], y[~val], X_val=X[val], y_val=y[val])
        n = probe.n_trees
    return M.BoostedTrees(max_iter=n).fit(X, y)


def fit_all(train: pd.DataFrame, y: np.ndarray, features, n_trees: int) -> dict:
    meta = train[["site_id", "date", "lat", "lon"]]
    X = train[features].to_numpy(float)
    lr, gb = M.Logistic().fit(X, y), fit_boosted(train, y, features, n_trees)
    return {
        "climatology": M.Climatology().fit(X, y, meta),
        "logistic": lr,
        "boosted_trees": gb,
        "blend": M.Blend(lr, gb),
    }


def predict_all(models: dict, test: pd.DataFrame, features) -> dict:
    meta = test[["site_id", "date", "lat", "lon"]]
    X = test[features].to_numpy(float)
    return {name: m.predict(X, meta) for name, m in models.items()}


def feature_importance(model, X: np.ndarray, seed: int = 0) -> np.ndarray:
    """How much does the model's score move when one feature is shuffled? (cheap, label-free)"""
    rng = np.random.default_rng(seed)
    X = X[rng.choice(len(X), min(len(X), 3000), replace=False)]
    base = model.raw_score(X)
    out = np.zeros(X.shape[1])
    for j in range(X.shape[1]):
        Xs = X.copy()
        Xs[:, j] = rng.permutation(Xs[:, j])
        out[j] = np.mean(np.abs(model.raw_score(Xs) - base))
    return out


def week_bootstrap_diff(y, p_model, p_base, weeks, n_boot=N_BOOT, seed=0) -> tuple[float, float]:
    """95% interval of PR-AUC(model) - PR-AUC(base), resampling whole weeks."""
    rng = np.random.default_rng(seed)
    uniq = np.unique(weeks)
    rows_by_week = [np.flatnonzero(weeks == w) for w in uniq]
    diffs = []
    for _ in range(n_boot):
        idx = np.concatenate([rows_by_week[i] for i in rng.integers(0, len(uniq), len(uniq))])
        if y[idx].sum() == 0:
            continue
        diffs.append(M.pr_auc(y[idx], p_model[idx]) - M.pr_auc(y[idx], p_base[idx]))
    return float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))


def run_split(df: pd.DataFrame, split: str, features, n_trees: int, sites) -> list[dict]:
    """Score every target and level on one split. Returns a list of result rows."""
    if split == "time":
        folds = [(df["year"].isin(TRAIN_YEARS), df["year"].isin(TEST_YEARS))]
    else:
        groups = neighbor_groups(sites)
        g = df["site_id"].map(groups)
        # 3 folds of neighbor groups (all of a group stays together)
        fold_of_group = {grp: i % 3 for i, grp in enumerate(sorted(set(groups.values())))}
        f = g.map(fold_of_group)
        folds = [((f != k) & df["year"].isin(TRAIN_YEARS), (f == k) & df["year"].isin(TEST_YEARS))
                 for k in range(3)]

    results = []
    for hz in TARGETS:
        for level in LEVELS:
            ys, preds, weeks, importances = [], {}, [], []
            for tr_mask, te_mask in folds:
                train, test = df[tr_mask], df[te_mask]
                ytr = target(train, hz, level)
                if ytr.sum() < 10:
                    continue
                models = fit_all(train, ytr, features, n_trees)
                for name, p in predict_all(models, test, features).items():
                    preds.setdefault(name, []).append(p)
                ys.append(target(test, hz, level))
                weeks.append(np.array([d.isocalendar()[0] * 100 + d.isocalendar()[1] for d in test["date"]]))
                importances.append(feature_importance(models["boosted_trees"], test[features].to_numpy(float)))
            if not ys:
                continue
            y = np.concatenate(ys)
            P = {k: np.concatenate(v) for k, v in preds.items()}
            wk = np.concatenate(weeks)
            imp = np.sum(importances, axis=0)
            share = imp / imp.sum() if imp.sum() > 0 else imp
            row = dict(split=split, target=hz, level=level, n_test=len(y), positives=int(y.sum()),
                       base_rate=float(y.mean()),
                       top_features=[features[i] for i in np.argsort(-share)[:3]],
                       top_feature_share=float(share.max()) if len(share) else 0.0)
            for name, p in P.items():
                row[f"pr_auc_{name}"] = M.pr_auc(y, p)
                row[f"brier_{name}"] = M.brier(y, p)
                row[f"ece_{name}"] = M.ece(y, p) if y.sum() else float("nan")
            if y.sum() >= MIN_TEST_POSITIVES:
                row["diff_ci"] = week_bootstrap_diff(y, P["blend"], P["climatology"], wk)
            flags = []
            if y.sum() < MIN_TEST_POSITIVES:
                flags.append("NO SIGNAL")
            if max(row["pr_auc_boosted_trees"], row["pr_auc_blend"]) > TOO_GOOD_PR_AUC \
                    or row["top_feature_share"] > TOO_GOOD_FEATURE_SHARE:
                flags.append("TOO GOOD?")
            row["flags"] = flags
            results.append(row)
            print(f"  {split:10s} {hz:9s} >={level}: pos {int(y.sum()):5d}  "
                  f"clim {row['pr_auc_climatology']:.3f}  logit {row['pr_auc_logistic']:.3f}  "
                  f"trees {row['pr_auc_boosted_trees']:.3f}  blend {row['pr_auc_blend']:.3f}  {' '.join(flags)}", flush=True)
    return results


def markdown_table(results: list[dict]) -> str:
    lines = ["| split | hazard | level | test yes-days | base rate | PR-AUC climatology | PR-AUC logistic | "
             "PR-AUC trees | PR-AUC blend | blend - clim (95% CI) | ECE blend | ECE clim | top feature (trees) | flags |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        ci = r.get("diff_ci")
        ci_s = f"{r['pr_auc_blend'] - r['pr_auc_climatology']:+.3f} ({ci[0]:+.3f}, {ci[1]:+.3f})" if ci else "n/a"
        lines.append(
            f"| {r['split']} | {r['target']} | >={r['level']} | {r['positives']} | {r['base_rate']:.3f} | "
            f"{r['pr_auc_climatology']:.3f} | {r['pr_auc_logistic']:.3f} | {r['pr_auc_boosted_trees']:.3f} | "
            f"{r['pr_auc_blend']:.3f} | {ci_s} | {r['ece_blend']:.3f} | {r['ece_climatology']:.3f} | "
            f"{r['top_features'][0]} ({r['top_feature_share']:.0%}) | {', '.join(r['flags'])} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Score the hazard index models")
    ap.add_argument("--sites", default=None)
    ap.add_argument("--quick", action="store_true", help="fewer trees, time split only")
    ap.add_argument("--drop", default="", help="comma-separated feature prefixes to leave out (ablation)")
    args = ap.parse_args()
    sites = load_sites(args.sites)
    df = load_dataset(sites)
    drop = [p for p in args.drop.split(",") if p]
    features = [f for f in FEATURES if not any(f.startswith(p) for p in drop)]
    n_trees = 150 if args.quick else 400
    print(f"{df['site_id'].nunique()} sites, {len(df)} site-days, {len(features)} features"
          + (f" (dropped {drop})" if drop else ""))
    results = []
    for split in (["time"] if args.quick else ["time", "time+space"]):
        results += run_split(df, split, features, n_trees, sites)
    tag = "_no_" + "_".join(drop) if drop else ""
    out = fetch.data_dir() / f"eval_results{tag}.json"
    out.write_text(json.dumps(results, indent=1, default=str))
    print()
    print(markdown_table(results))
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
