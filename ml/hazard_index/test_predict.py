"""predict_index on made-up inputs (no network), using the committed model file."""

import datetime as dt

import pytest

from ml.hazard_index import hazards as H
from ml.hazard_index.predict import MODEL_PATH, band, predict_index
from ml.hazard_index.test_features import FACTS, SITE, fake_inputs

pytestmark = pytest.mark.skipif(not MODEL_PATH.exists(), reason="train the model first: python -m ml.hazard_index.train")


def _inputs(seed=1):
    weather, warnings, lsrs, spc = fake_inputs(seed)
    return dict(weather=weather, warnings=warnings, lsrs=lsrs, spc=spc, facts=FACTS, mode="archive")


def test_shape_and_ranges():
    r = predict_index("gnv", dt.date(2024, 6, 8), sites=[SITE], inputs=_inputs())
    assert 0 <= r["index"] <= 10 and r["band"] == band(r["index"])
    assert set(r["hazards"]) == set(H.HAZARDS)
    for h in r["hazards"].values():
        assert 0 <= h["index"] <= 10
        assert h["p_advisory"] >= h["p_warning"] >= h["p_damage"]       # higher levels can't be likelier
    assert r["cutoff"] == "2024-06-07T18:00:00-04:00"
    assert len(r["top_reasons"]) <= 3 and all(isinstance(x["text"], str) for x in r["top_reasons"])
    assert any("not in training" in n for n in r["notes"])               # "gnv" is a made-up site


def test_heat_wave_scores_higher_heat_than_cool_day():
    hot, cool = _inputs(), _inputs()
    hot["weather"] = hot["weather"].assign(temperature_2m=37.0, relative_humidity_2m=60.0)
    cool["weather"] = cool["weather"].assign(temperature_2m=20.0, relative_humidity_2m=50.0)
    day = dt.date(2024, 6, 8)
    h = predict_index("gnv", day, sites=[SITE], inputs=hot)["hazards"]["heat"]["index"]
    c = predict_index("gnv", day, sites=[SITE], inputs=cool)["hazards"]["heat"]["index"]
    assert h > c + 0.5
