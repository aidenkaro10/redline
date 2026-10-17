from ml.data import load_split
from ml.final_cv import N_FOLDS, folds_of


def test_folds_cover_the_pool_once_and_never_touch_test_people():
    people = [f"p{i}" for i in range(55)]
    folds = folds_of(people, N_FOLDS, seed=1)
    flat = [p for f in folds for p in f]
    assert sorted(flat) == sorted(people) and len(flat) == len(set(flat))
    assert all(len(f) == 11 for f in folds)


def test_real_pool_excludes_test_people():
    split = load_split("mendeley")
    pool = split["train"] + split["val"] + split["calib"]
    assert not set(pool) & set(split["test"])
    assert len(set(pool)) == 55
