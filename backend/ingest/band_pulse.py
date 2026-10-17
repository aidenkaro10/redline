"""
Heart rate from the band's own light sensor (PPG), worked out on the laptop.

The band sends raw light readings (PPG:10 numbers, 50 per second) and accelerometer
readings (ACC:x,y,z, 50 per second). Every BAND_PPG_STEP_S (5 s) we look at the last
BAND_PPG_WINDOW_S (10 s) and either give ONE heart rate with a quality score, or nothing.
We never fill in a guess: no number is better than a made-up number.

How one heart rate is found (estimate_window):
  1. Clean up: remove the slow drift and keep only 0.7-3.5 Hz (42-210 bpm), where a pulse lives.
  2. Accelerometer: how hard the arm moves, and at what rhythms (arm swing, steps).
  3. Subtract the part of the light signal that follows the accelerometer (NLMS noise
     canceller). On by default: on our test data it took walking from ~1% to ~40% of
     windows with a heart rate (docs/sessions/band-sensors.md).
  4. Cut the arm's rhythms (and their multiples) out of the spectrum. The strongest rhythm
     left is the heart rate candidate. If the raw signal was mostly the arm and nothing
     clear is left, we give no heart rate ("cadence_lock").
  5. Autocorrelation check: if the signal really repeats every 60/HR seconds, it lines up
     with itself shifted by one beat. The spectrum and this check have to agree.
  6. Quality 0-1 from: how clear the peak is, how well the beat repeats, how much the
     arm moves, and the cadence penalty. Below BAND_PPG_MIN_QUALITY -> nothing.

Accepted heart rates go to the hub as Reading(hr=..., source="band_ppg"); the pipeline
treats them as the band pulse sensor (Kalman noise KALMAN_SIGMA_BAND_PPG).
"""

from collections import deque
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt

from backend import config

FS = config.BAND_SAMPLE_HZ
NFFT = 4096                                 # zero-padded FFT: ~0.7 bpm between frequency bins
# Half-width of the cut around each arm rhythm. A 10 s window blurs every rhythm by about
# +-2/window Hz (Hann window main lobe), so anything narrower leaves arm noise behind.
NOTCH_HZ = 2.0 / config.BAND_PPG_WINDOW_S
_SOS = butter(3, [config.BAND_PPG_LOW_HZ, config.BAND_PPG_HIGH_HZ], btype="bandpass", fs=FS, output="sos")
_MIN_BPM, _MAX_BPM = 60 * config.BAND_PPG_LOW_HZ, 60 * config.BAND_PPG_HIGH_HZ


@dataclass
class PulseResult:
    """One window's answer. hr is None when we refuse to give a number."""
    t: float                         # laptop time of the end of the window
    hr: float | None                 # bpm, or None
    quality: float                   # 0-1
    reason: str                      # "ok", or why there's no number
    motion_g: float | None = None    # arm movement in the window (g), None = no accelerometer
    cadence_bpm: float | None = None # arm-swing / step rhythm, per minute
    candidate_bpm: float | None = None   # best guess before the quality check (for debugging/eval only)
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def bandpass(x: np.ndarray) -> np.ndarray:
    """Remove the mean and drift, keep 0.7-3.5 Hz. Works on 1-D or (n, axes) arrays."""
    x = np.asarray(x, dtype=float)
    x = x - np.mean(x, axis=0)
    return sosfiltfilt(_SOS, x, axis=0)


def spectrum(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Power at each frequency (Hann window, zero padded). x: 1-D, or (n, axes) summed over axes."""
    w = np.hanning(len(x))
    if x.ndim == 2:
        w = w[:, None]
    p = np.abs(np.fft.rfft(x * w, n=NFFT, axis=0)) ** 2
    if p.ndim == 2:
        p = p.sum(axis=1)
    return np.fft.rfftfreq(NFFT, 1 / FS), p


def autocorr(x: np.ndarray) -> np.ndarray:
    """Normalized autocorrelation (1.0 at lag 0), unbiased so long lags aren't shrunk."""
    x = x - x.mean()
    n = len(x)
    f = np.fft.rfft(x, n=2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    ac = ac / (n - np.arange(n))
    return ac / ac[0] if ac[0] > 0 else ac


def _ac_at(ac: np.ndarray, lag_s: float) -> float:
    """Autocorrelation at a lag in seconds (linear interpolation between samples)."""
    pos = lag_s * FS
    i = int(pos)
    if i + 1 >= len(ac):
        return 0.0
    return float(ac[i] + (pos - i) * (ac[i + 1] - ac[i]))


def nlms_cancel(ppg: np.ndarray, refs: np.ndarray, w: np.ndarray | None = None,
                taps: int = 8, mu: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    """
    NLMS adaptive noise canceller. refs: (n, k) accelerometer axes (band-passed).
    It learns how the arm movement shows up in the light signal and subtracts that.
    Returns (cleaned signal, filter weights to carry over to the next window).
    """
    n, k = refs.shape
    if w is None or w.shape != (taps * k,):
        w = np.zeros(taps * k)
    padded = np.vstack([np.zeros((taps - 1, k)), refs])
    out = np.empty(n)
    for i in range(n):
        u = padded[i:i + taps][::-1].ravel()          # the last `taps` samples of every axis
        y = w @ u
        e = ppg[i] - y
        w = w + mu * e * u / (u @ u + 1e-6)
        out[i] = e
    return out, w


def nlms_refs(acc: np.ndarray) -> np.ndarray:
    """
    What the noise canceller listens to: the three accelerometer axes, plus
    the arm's movement energy (sum of squared wobble), because the sensor pressing on the skin
    harder doesn't follow the arm in a straight line.
    """
    axes = bandpass(acc)
    energy = bandpass(np.sum((acc - acc.mean(axis=0)) ** 2, axis=1))
    return np.column_stack([axes, energy])


def motion_rhythm(acc: np.ndarray) -> tuple[float, float, list[float]]:
    """
    (movement in g, main rhythm in Hz, list of the arm's rhythms in Hz).
    Movement = how much the three axes wobble around their average (rotating the wrist
    counts too, not just shaking). The rhythms are the accelerometer's strongest peaks.
    An arm moving at f shakes the light sensor at f, 2f, 3f ... (steps land at 2x the
    arm swing), so the caller cuts out those multiples too.
    """
    acc = np.asarray(acc, dtype=float)
    motion_g = float(np.sqrt(np.sum(np.var(acc, axis=0))))
    if motion_g < config.BAND_STILL_G:
        return motion_g, 0.0, []
    f, p = spectrum(bandpass(acc))
    band = (f >= config.BAND_PPG_LOW_HZ / 2) & (f <= config.BAND_PPG_HIGH_HZ)
    fb, pb = f[band], p[band]
    peaks, _ = find_peaks(pb)
    if len(peaks) == 0:
        return motion_g, 0.0, []
    peaks = peaks[np.argsort(pb[peaks])[::-1]]
    main = float(fb[peaks[0]])
    rhythms = [float(fb[i]) for i in peaks[:4] if pb[i] >= 0.05 * pb[peaks[0]]]
    return motion_g, main, rhythms


# ---------------------------------------------------------------------------
# One window -> one heart rate (or none)
# ---------------------------------------------------------------------------
def estimate_window(ppg_raw: np.ndarray, acc: np.ndarray | None = None, t: float = 0.0,
                    prev_hr: float | None = None, nlms_state: dict | None = None) -> PulseResult:
    """
    ppg_raw: raw counts for the window (BAND_PPG_WINDOW_S * 50 samples).
    acc: (n, 3) accelerometer samples for about the same time, or None if the band has none.
    prev_hr: the last heart rate we accepted in the last ~30 s (helps pick between two peaks).
    nlms_state: a dict to keep the noise canceller's weights between windows; None = canceller off.
    """
    x = np.asarray(ppg_raw, dtype=float)
    if len(x) < FS * 4:
        return PulseResult(t, None, 0.0, "too_short")
    clipped = np.mean((x <= 0) | (x >= 4095))
    if clipped > config.BAND_PPG_CLIPPED_SHARE:
        return PulseResult(t, None, 0.0, "clipped")          # sensor off the skin or too much light
    if np.std(x) < 1.0:
        return PulseResult(t, None, 0.0, "flat")             # nothing changing: no pulse in view

    xf = bandpass(x)
    motion_g, cadence_hz, rhythms = None, 0.0, []
    has_acc = acc is not None and len(acc) >= 0.8 * len(x)
    if has_acc:
        acc = np.asarray(acc, dtype=float)[-len(x):]
        if len(acc) != len(x):                       # a few ACC lines lost: stretch to line up with the PPG
            src = np.linspace(0, 1, len(acc))
            acc = np.column_stack([np.interp(np.linspace(0, 1, len(x)), src, acc[:, k]) for k in range(3)])
        motion_g, cadence_hz, rhythms = motion_rhythm(acc)
        if nlms_state is not None and motion_g >= config.BAND_STILL_G:
            xf, nlms_state["w"] = nlms_cancel(xf, nlms_refs(acc), nlms_state.get("w"))

    tol_hz = config.BAND_CADENCE_MATCH_BPM / 60
    moving = motion_g is not None and motion_g >= 2 * config.BAND_STILL_G
    # Cut out every multiple of every arm rhythm (arm swing f -> steps at 2f -> ...).
    notches = []
    if moving:
        notches = sorted({k * r for r in rhythms for k in range(1, 6)
                          if config.BAND_PPG_LOW_HZ - NOTCH_HZ <= k * r <= config.BAND_PPG_HIGH_HZ + NOTCH_HZ})

    f, p = spectrum(xf)
    band = (f >= config.BAND_PPG_LOW_HZ) & (f <= config.BAND_PPG_HIGH_HZ)
    fb, pb_raw = f[band], p[band]
    total_raw = pb_raw.sum()
    if total_raw <= 0:
        return PulseResult(t, None, 0.0, "no_peak", motion_g, 60 * cadence_hz or None)
    # Was the raw signal mostly the arm? (its strongest rhythm sits on the arm's rhythm)
    raw_top = float(fb[np.argmax(pb_raw)])
    arm_dominated = moving and any(abs(raw_top - r) <= NOTCH_HZ for r in notches)

    # Cut the arm's rhythms out (spectral subtraction): what's left is pulse + leftover noise.
    on_arm = np.zeros(len(f), dtype=bool)
    for r in notches:
        on_arm |= np.abs(f - r) <= NOTCH_HZ
    pb = np.where(on_arm[band], 0.0, pb_raw)
    total = pb.sum()
    kept_share = float(total / total_raw)                   # how much of the signal was NOT the arm
    peaks, _ = find_peaks(pb)
    if moving and cadence_hz:
        # While the arm is really moving, the heart beats faster than the arm swings. Leftover
        # arm noise piles up at and below the arm's rhythm, so only look above it (+ a margin).
        # A real heart rate down there just gives "no number", never a wrong one.
        peaks = peaks[fb[peaks] >= cadence_hz + 2 * NOTCH_HZ]
    if total <= 0 or len(peaks) == 0:
        return PulseResult(t, None, 0.0, "cadence_lock" if moving else "no_peak",
                           motion_g, 60 * cadence_hz or None)
    peaks = peaks[np.argsort(pb[peaks])[::-1]][:5]
    peaks = [i for i in peaks if pb[i] >= 0.1 * pb[peaks[0]]]

    # The same cut in the time signal, for the "does it repeat every beat" check.
    if notches:
        spec = np.fft.rfft(xf, n=NFFT)
        spec[on_arm] = 0
        xc = np.fft.irfft(spec, n=NFFT)[:len(xf)]
    else:
        xc = xf
    ac = autocorr(xc)

    def peak_share(i: int) -> float:
        """Share of the remaining 42-210 bpm power that sits in this peak (+-0.15 Hz)."""
        near = np.abs(fb - fb[i]) <= 0.15
        return float(pb[near].sum() / total)

    best = None
    for i in peaks:
        hz = float(fb[i])
        share = peak_share(i)
        rep = max(0.0, _ac_at(ac, 1 / hz))                    # does the signal repeat every beat?
        near_arm = any(abs(hz - r) <= NOTCH_HZ + tol_hz for r in notches)
        score = share * (0.3 + 0.7 * rep)
        if prev_hr is not None:
            score *= 0.4 + 0.6 * np.exp(-((60 * hz - prev_hr) / 15) ** 2)
        if best is None or score > best["score"]:
            best = {"score": score, "hz": hz, "share": share, "rep": rep, "near_arm": near_arm}

    # Refine the frequency between FFT bins (parabola through the peak and its neighbors).
    hz = best["hz"]
    i = int(np.argmin(np.abs(fb - hz)))
    if 0 < i < len(pb) - 1 and min(pb[i - 1], pb[i + 1]) > 0:
        a, b, c = pb[i - 1], pb[i], pb[i + 1]
        denom = a - 2 * b + c
        if denom != 0:
            hz += 0.5 * (a - c) / denom * (fb[1] - fb[0])
    bpm = 60 * hz

    # Quality: clear peak x repeats every beat x calm arm x enough signal left after the cut.
    q_peak = min(1.0, best["share"] / 0.45)
    q_rep = float(np.clip(best["rep"], 0, 1))
    q_motion = 1.0 if motion_g is None else 1 / (1 + (motion_g / config.BAND_HEAVY_MOTION_G) ** 2)
    q_kept = min(1.0, kept_share / 0.3) if notches else 1.0
    quality = (q_peak ** 0.5) * (q_rep ** 0.7) * q_motion * (q_kept ** 0.5)
    if not has_acc:
        quality *= 0.85                                      # can't rule out arm noise without an accelerometer
    quality = float(np.clip(quality, 0, 1))
    details = {"q_peak": round(q_peak, 3), "q_rep": round(q_rep, 3), "q_motion": round(q_motion, 3),
               "q_kept": round(q_kept, 3), "arm_dominated": arm_dominated, "near_arm": best["near_arm"]}
    cadence_bpm = 60 * cadence_hz if cadence_hz else None
    if quality < config.BAND_PPG_MIN_QUALITY:
        reason = ("cadence_lock" if arm_dominated or best["near_arm"]
                  else "heavy_motion" if q_motion < 0.6 else "unclear")
        return PulseResult(t, None, quality, reason, motion_g, cadence_bpm, bpm, details)
    return PulseResult(t, round(bpm, 1), quality, "ok", motion_g, cadence_bpm, bpm, details)


def naive_peak_hr(ppg_raw: np.ndarray) -> float | None:
    """
    The simple way, kept only as the comparison baseline in the evaluation: band-pass,
    count the beats (peaks at least 0.3 s apart that stand out), beats per minute. Always answers.
    """
    x = np.asarray(ppg_raw, dtype=float)
    if len(x) < FS * 2 or np.std(x) == 0:
        return None
    xf = -bandpass(x)                     # beats are dips in the raw light reading
    # at least 0.3 s apart (max 200 bpm) and standing out a bit, so the small second bump isn't counted
    peaks, _ = find_peaks(xf, distance=int(0.3 * FS), prominence=0.5 * np.std(xf))
    if len(peaks) < 2:
        return None
    return float(60 * (len(peaks) - 1) / ((peaks[-1] - peaks[0]) / FS))


# ---------------------------------------------------------------------------
# Streaming: feed lines as they arrive
# ---------------------------------------------------------------------------
class PulseEstimator:
    """
    One per worker. Feed PPG lines (10 samples each) and ACC samples as they arrive;
    add_ppg() returns a PulseResult every BAND_PPG_STEP_S once the window is full, else None.
    """

    def __init__(self, use_nlms: bool = True):
        self.window = int(config.BAND_PPG_WINDOW_S * FS)
        self.step = int(config.BAND_PPG_STEP_S * FS)
        self.ppg: deque = deque(maxlen=self.window)
        self.acc: deque = deque(maxlen=self.window)
        self.new_samples = 0
        self.last_line_t: float | None = None
        self.last_hr: float | None = None
        self.last_hr_t: float | None = None
        self.nlms = {} if use_nlms else None

    def add_acc(self, t: float, xyz: tuple[float, float, float]) -> None:
        self.acc.append(xyz)

    def reset(self) -> None:
        self.ppg.clear()
        self.acc.clear()
        self.new_samples = 0

    def add_ppg(self, t: float, samples) -> PulseResult | None:
        if self.last_line_t is not None and t - self.last_line_t > config.BAND_PPG_GAP_S:
            self.reset()                       # lines went missing: don't stitch two different moments together
        self.last_line_t = t
        self.ppg.extend(samples)
        self.new_samples += len(samples)
        if len(self.ppg) < self.window or self.new_samples < self.step:
            return None
        self.new_samples = 0
        prev = self.last_hr if self.last_hr_t is not None and t - self.last_hr_t <= 30 else None
        acc = np.array(self.acc) if len(self.acc) else None
        result = estimate_window(np.array(self.ppg), acc, t, prev, self.nlms)
        if result.hr is not None:
            self.last_hr, self.last_hr_t = result.hr, t
        return result
