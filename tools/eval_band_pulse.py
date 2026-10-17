"""
Score the band's laptop-side heart rate (backend/ingest/band_pulse.py) before we trust it.

1. Synthetic: PPG + accelerometer made from KNOWN heart rate traces (backend/ingest/band_synth.py)
   at rest, walking and arm work. Every 5 s we compare each method's answer with the true
   average heart rate over the same 10 s window.
2. Real recordings (when they exist): data/recordings/band_*.txt from tools/record_band.py
   (laptop time TAB band line). Reference heart rates come from a note file next to them
   (data/recordings/band_notes.txt or notes.txt, lines like "sit: 72" or "band_walk.txt 95").
   Real files have no second-by-second truth, so we compare the median estimate with the note.

Methods compared:
  naive       count beats in the band-passed signal (always answers)
  spectral    our method without the accelerometer (spectrum + autocorrelation)
  +acc        + accelerometer: cut out the arm's rhythms, motion penalty
  +acc+nlms   + NLMS noise canceller fed by the accelerometer   <- what the server runs

Reported per activity: MAE (bpm, only over windows where the method gave a number),
coverage (% of 10 s windows with a number), minutes with at least one number (what the
Kalman sees: it uses 1-minute averages), and % of given numbers off by more than 10 bpm.

Run:
    .venv/bin/python tools/eval_band_pulse.py                 # report seeds (held out from tuning)
    .venv/bin/python tools/eval_band_pulse.py --tune          # the seeds used while tuning
    .venv/bin/python tools/eval_band_pulse.py --markdown      # print the table as markdown
"""

import argparse
import os
import re
import statistics
import sys
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "2")           # keep numpy/BLAS to 2 threads (other sessions share the Mac)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from backend import config  # noqa: E402
from backend.app.schemas import parse_line  # noqa: E402
from backend.ingest import band_synth  # noqa: E402
from backend.ingest.band_pulse import PulseEstimator, estimate_window, naive_peak_hr  # noqa: E402

FS = config.BAND_SAMPLE_HZ
WIN = int(config.BAND_PPG_WINDOW_S * FS)
STEP = int(config.BAND_PPG_STEP_S * FS)
METHODS = ("naive", "spectral", "+acc", "+acc+nlms")
TUNE_SEEDS = range(0, 20)          # used while choosing the numbers in config.py
REPORT_SEEDS = range(1000, 1030)   # never looked at while tuning


def run_session(rec: dict) -> dict[str, list[tuple[float, float | None]]]:
    """For one synthetic session: {method: [(true bpm, estimate or None), ...]} every 5 s."""
    ppg, acc, hr = rec["ppg"], rec["acc"], rec["hr"]
    out = {m: [] for m in METHODS}
    prev = {m: None for m in METHODS}
    nlms_state: dict = {}
    for end in range(WIN, len(ppg) + 1, STEP):
        truth = float(np.mean(hr[(end - WIN) // FS:end // FS]))
        win_p, win_a = ppg[end - WIN:end], acc[end - WIN:end]
        out["naive"].append((truth, naive_peak_hr(win_p)))
        for m, a, state in (("spectral", None, None), ("+acc", win_a, None), ("+acc+nlms", win_a, nlms_state)):
            r = estimate_window(win_p, a, end / FS, prev[m], state)
            out[m].append((truth, r.hr))
            prev[m] = r.hr if r.hr is not None else prev[m]
    return out


PER_MIN = 60 // config.BAND_PPG_STEP_S          # 12 windows a minute


def score(sessions: list[list[tuple[float, float | None]]]) -> dict:
    pairs = [p for s in sessions for p in s]
    given = [(t, e) for t, e in pairs if e is not None]
    errs = [abs(e - t) for t, e in given]
    minutes = [s[i:i + PER_MIN] for s in sessions for i in range(0, len(s) - PER_MIN + 1, PER_MIN)]
    return {"mae": float(np.mean(errs)) if errs else float("nan"),
            "coverage": 100 * len(given) / max(len(pairs), 1),
            "minutes": 100 * float(np.mean([any(e is not None for _, e in m) for m in minutes])) if minutes else float("nan"),
            "bad": 100 * float(np.mean([x > 10 for x in errs])) if errs else float("nan"),
            "n": len(pairs)}


def synthetic(seeds, seconds: int = 300) -> dict[str, dict[str, dict]]:
    table = {}
    for kind in band_synth.KINDS:
        pooled = {m: [] for m in METHODS}
        for seed in seeds:
            rec = band_synth.session(kind, seconds, seed=seed * 7 + band_synth.KINDS.index(kind))
            for m, pairs in run_session(rec).items():
                pooled[m].append(pairs)
        table[kind] = {m: score(p) for m, p in pooled.items()}
    return table


# ---------------------------------------------------------------------------
# Real recordings
# ---------------------------------------------------------------------------
def read_recording(path: Path) -> list[tuple[float, str]]:
    """Lines 'laptop_time<TAB>band line' -> [(time, line)]. Skips anything malformed."""
    rows = []
    for raw in path.read_text(errors="replace").splitlines():
        if "\t" not in raw:
            continue
        t, line = raw.split("\t", 1)
        try:
            rows.append((float(t), line))
        except ValueError:
            continue
    return rows


def reference_hrs(folder: Path) -> dict[str, float]:
    """Find 'sit: 72' / 'band_walk.txt 95 bpm' style notes. Key = activity word (sit, walk, arms, fall)."""
    refs = {}
    for name in ("band_notes.txt", "notes.txt", "band_notes.md", "README.md"):
        p = folder / name
        if not p.exists():
            continue
        for line in p.read_text(errors="replace").splitlines():
            m = re.search(r"(?:band_)?(sit|walk|arms|fall)\w*(?:\.txt)?\D+(\d{2,3})", line, re.I)
            if m:
                refs[m.group(1).lower()] = float(m.group(2))
    return refs


def run_recording(path: Path) -> dict:
    """Stream one recording through the same code the server uses. Returns the estimates per method."""
    est = PulseEstimator()
    ests, naive, ppg_all = [], [], []
    for t, line in read_recording(path):
        fields = parse_line(line) or {}
        if "accel" in fields:
            est.add_acc(t, fields["accel"])
        if "ppg" in fields:
            ppg_all.extend(fields["ppg"])
            r = est.add_ppg(t, fields["ppg"])
            if r is not None:
                ests.append(r)
                naive.append(naive_peak_hr(np.array(ppg_all[-WIN:])))
    return {"ours": [r.hr for r in ests], "naive": naive, "windows": len(ests),
            "reasons": {k: sum(r.reason == k for r in ests) for k in {r.reason for r in ests}}}


def recordings(folder: Path = Path("data/recordings")) -> list[dict]:
    refs = reference_hrs(folder)
    rows = []
    for path in sorted(folder.glob("band_*.txt")):
        r = run_recording(path)
        key = next((k for k in ("sit", "walk", "arms", "fall") if k in path.stem), None)
        ref = refs.get(key)
        row = {"file": path.name, "windows": r["windows"], "reference": ref, "reasons": r["reasons"]}
        for m in ("naive", "ours"):
            vals = [v for v in r[m] if v is not None]
            row[m] = {"median": statistics.median(vals) if vals else None,
                      "coverage": 100 * len(vals) / max(r["windows"], 1),
                      "mae_vs_ref": (float(np.mean([abs(v - ref) for v in vals])) if vals and ref else None)}
        rows.append(row)
    return rows


def markdown(table: dict) -> str:
    lines = ["| Activity | Method | MAE (bpm) | 10 s windows with a number | Minutes with a number | Off by >10 bpm |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]
    for kind, by_method in table.items():
        for m, s in by_method.items():
            lines.append(f"| {kind} | {m} | {s['mae']:.1f} | {s['coverage']:.0f}% | {s['minutes']:.0f}% | {s['bad']:.0f}% |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--tune", action="store_true", help="score the tuning seeds instead of the held-out ones")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--seconds", type=int, default=300)
    a = ap.parse_args(argv)
    seeds = TUNE_SEEDS if a.tune else REPORT_SEEDS
    table = synthetic(seeds, a.seconds)
    print(f"Synthetic, {len(seeds)} sessions x {a.seconds} s per activity "
          f"({'tuning' if a.tune else 'held-out'} seeds)")
    if a.markdown:
        print(markdown(table))
    else:
        for kind, by_method in table.items():
            for m, s in by_method.items():
                print(f"  {kind:5} {m:10} MAE {s['mae']:5.1f} bpm  given {s['coverage']:5.1f}%  "
                      f"minutes {s['minutes']:5.1f}%  >10 bpm off {s['bad']:5.1f}%   ({s['n']} windows)")
    rows = recordings()
    if not rows:
        print("\nNo real recordings yet (data/recordings/band_*.txt).")
    for row in rows:
        print(f"\nReal: {row['file']} ({row['windows']} windows, reference {row['reference']} bpm)")
        for m in ("naive", "ours"):
            s = row[m]
            print(f"  {m:6} median {s['median']}  given {s['coverage']:.0f}%  MAE vs reference {s['mae_vs_ref']}")
        print(f"  why no number: {row['reasons']}")


if __name__ == "__main__":
    main()
