"""
Train the final hazard index model on ALL years and save it as one JSON file.

The evaluation (evaluate.py) holds out 2023-2024 to get honest scores. For the
model we actually ship, we then retrain on everything (2018-2024), because
more recent years help and the scores have already been measured.

For every hazard (and "overall") and every level on the ladder (advisory 3,
warning 5, damage 8) there is one model answering "will it reach this level
tomorrow?": the blend of logistic regression and boosted trees (SHIPPED_KIND). Climatology tables are saved too, as a fallback and as
the baseline shown next to the model.

Output: ml/hazard_index/model/hazard_index.joblib (scikit-learn models + tables).
        Load it with the same scikit-learn version (see requirements.txt).
Run:    python -m ml.hazard_index.train
"""

import argparse
import datetime as dt
from pathlib import Path

import joblib
import numpy as np

from ml.hazard_index import hazards as H
from ml.hazard_index import models as M
from ml.hazard_index.evaluate import LEVELS, TARGETS, fit_boosted, load_dataset, target
from ml.hazard_index.features import FEATURES
from ml.hazard_index.sites import load_sites

MODEL_PATH = Path(__file__).resolve().parent / "model" / "hazard_index.joblib"
N_TREES = 400
SHIPPED_KIND = "blend"       # "blend", "boosted_trees" or "logistic": whichever won in evaluate.py


def main():
    ap = argparse.ArgumentParser(description="Train the shipped hazard index model")
    ap.add_argument("--sites", default=None)
    args = ap.parse_args()
    sites = load_sites(args.sites)
    df = load_dataset(sites)
    years = sorted(df["year"].unique())
    print(f"Training on {df['site_id'].nunique()} sites, {years[0]}-{years[-1]}, {len(df)} site-days")

    out = dict(version=dt.date.today().isoformat(), kind=SHIPPED_KIND, features=FEATURES, levels=list(LEVELS),
               train_years=[int(years[0]), int(years[-1])], sites=sorted(df["site_id"].unique().tolist()),
               site_source="placeholder" if all(s.site_id.startswith("ph-") for s in sites) else "plants.geojson",
               models={}, climatology={}, death_given_damage={})
    for hz in TARGETS:
        out["models"][hz], out["climatology"][hz] = {}, {}
        for level in LEVELS:
            y = target(df, hz, level)
            if y.sum() < 10:
                print(f"  {hz} >={level}: only {int(y.sum())} yes-days, climatology only")
                out["models"][hz][str(level)] = None
            else:
                gb = fit_boosted(df, y, FEATURES, N_TREES, val_year=int(years[-1]))  # size picked on the last year
                lr = M.Logistic().fit(df[FEATURES].to_numpy(float), y)
                print(f"  {hz} >={level}: {int(y.sum())} yes-days, {gb.n_trees} trees")
                out["models"][hz][str(level)] = {"blend": M.Blend(lr, gb), "boosted_trees": gb,
                                                 "logistic": lr}[SHIPPED_KIND]
            meta = df[["site_id", "date", "lat", "lon"]]
            clim = M.Climatology().fit(None, y, meta)
            out["climatology"][hz][str(level)] = dict(
                month_rate={str(k): v for k, v in clim.month_rate.items()},
                site_month={f"{s}|{m}": v for (s, m), v in clim.site_month.items()})
        dmg = (df[hz] >= H.DAMAGE).sum()
        out["death_given_damage"][hz] = float((df[hz] >= H.DEATH).sum() / dmg) if dmg else 0.0
    out["site_xy"] = {s.site_id: [s.lat, s.lon] for s in sites}
    # feature medians: used to fill a feature that a live source failed to deliver
    out["feature_median"] = {f: (None if np.isnan(v) else float(v))
                             for f, v in zip(FEATURES, np.nanmedian(df[FEATURES].to_numpy(float), axis=0))}
    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump(out, MODEL_PATH, compress=3)
    print(f"Saved {MODEL_PATH} ({MODEL_PATH.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
