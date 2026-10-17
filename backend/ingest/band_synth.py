"""
Synthetic band signals: what the band's light sensor (PPG) and accelerometer would
send for a known heart rate. Used by the fake band (backend/ingest/fake_band.py) and
by the pulse evaluation (tools/eval_band_pulse.py), so both run with no hardware.

The hard parts of a cheap wrist pulse sensor are built in on purpose (same ideas as
ml/simulate.py add_ppg_motion_artifacts):
  - motion artifact: arm movement shakes the sensor against the skin. The artifact
    follows the accelerometer (so the laptop can partly cancel it), plus a part that
    does NOT follow it (skin slipping), plus a squared term (not linear)
  - cadence lock: while walking, the artifact at the step / arm-swing rhythm is often
    BIGGER than the pulse itself, so a naive beat counter counts steps
  - effort noise: more random noise the harder the person works
  - dropouts: short stretches where the sensor loses the skin (stuck at 4095 or 0)

Activities ("kind"):
  rest   sitting, arm on a table
  walk   walking, arm swinging at ~0.85-1.0 Hz (steps at twice that)
  arms   arm work (carrying, shoveling): irregular big arm movements 0.5-1.6 Hz

Nothing here is used to make a safety decision. It only makes test data.
"""

import numpy as np

FS = 50                      # samples per second, same as the band (one sample every 20 ms)
KINDS = ("rest", "walk", "arms")


# ---------------------------------------------------------------------------
# Heart rate traces (one value per second, bpm)
# ---------------------------------------------------------------------------
def hr_trace(kind: str, seconds: int, rng: np.random.Generator) -> np.ndarray:
    """A believable heart rate for this activity: a slow random walk around a level."""
    start, end, wander, lo, hi = {
        "rest": (rng.uniform(58, 78), None, 0.25, 50, 90),
        "walk": (rng.uniform(85, 100), rng.uniform(100, 120), 0.4, 75, 140),
        "arms": (rng.uniform(100, 120), rng.uniform(120, 150), 0.5, 85, 175),
    }[kind]
    hr = np.empty(seconds)
    level = start
    for i in range(seconds):
        target = start if end is None else start + (end - start) * i / max(seconds - 1, 1)
        level += rng.normal(0, wander) + 0.05 * (target - level)    # random walk pulled toward the target
        hr[i] = np.clip(level, lo, hi)
    return hr


# ---------------------------------------------------------------------------
# Accelerometer (g), shape (n, 3)
# ---------------------------------------------------------------------------
def _rotate(g: np.ndarray, angle: np.ndarray, axis: np.ndarray) -> np.ndarray:
    """Rotate the gravity vector g by angle (radians, per sample) around a unit axis (Rodrigues)."""
    c, s = np.cos(angle)[:, None], np.sin(angle)[:, None]
    return g * c + np.cross(axis, g) * s + axis * (axis @ g) * (1 - c)


def motion(kind: str, n: int, rng: np.random.Generator) -> tuple[np.ndarray, float]:
    """Accelerometer samples for n samples of this activity, and the arm-swing frequency (Hz, 0 = none)."""
    t = np.arange(n) / FS
    g = rng.normal(0, 1, 3)
    g = g / np.linalg.norm(g)                       # which way is down for this wrist position
    if g[2] < 0:
        g = -g
    axis = np.cross(g, [1.0, 0.0, 0.0])
    axis = axis / np.linalg.norm(axis)
    noise = rng.normal(0, 0.006, (n, 3))            # sensor noise
    if kind == "rest":
        drift = 0.03 * np.sin(2 * np.pi * rng.uniform(0.01, 0.03) * t + rng.uniform(0, 6))
        acc = _rotate(g, drift, axis) + noise
        return acc, 0.0
    if kind == "walk":
        f = rng.uniform(0.85, 1.0)                  # arm swing; steps at 2f
        f_t = f * (1 + 0.02 * np.sin(2 * np.pi * 0.05 * t))     # pace changes a little
        phase = 2 * np.pi * np.cumsum(f_t) / FS
        swing = np.radians(rng.uniform(20, 35)) * np.sin(phase)
        acc = _rotate(g, swing, axis)
        acc += np.outer(0.12 * np.cos(phase), axis)                     # swing (tangential) acceleration
        step = np.clip(np.sin(2 * phase), 0, None) ** 4                  # heel strikes at 2f
        acc += np.outer(rng.uniform(0.2, 0.35) * step, g)
        return acc + noise * 2, f
    # arms: several drifting frequencies, bursts of effort
    acc = np.tile(g, (n, 1)).astype(float)
    f0 = rng.uniform(0.6, 1.3)
    for k, amp in ((1, rng.uniform(0.45, 0.75)), (2, rng.uniform(0.1, 0.25))):
        f_t = k * f0 * (1 + 0.15 * np.sin(2 * np.pi * rng.uniform(0.01, 0.04) * t + rng.uniform(0, 6)))
        phase = 2 * np.pi * np.cumsum(f_t) / FS + rng.uniform(0, 6)
        direction = rng.normal(0, 1, 3)
        direction /= np.linalg.norm(direction)
        acc += np.outer(amp * np.sin(phase), direction)
    burst = 0.7 + 0.6 * (np.sin(2 * np.pi * rng.uniform(0.02, 0.06) * t) > 0)
    acc = g + (acc - g) * burst[:, None]
    return acc + noise * 3, f0


# ---------------------------------------------------------------------------
# PPG (raw analogRead counts, 0-4095)
# ---------------------------------------------------------------------------
def _lowpass(x: np.ndarray, seconds: float) -> np.ndarray:
    """Simple moving average (a cheap low-pass), same length as x."""
    k = max(1, int(seconds * FS))
    kernel = np.ones(k) / k
    if x.ndim == 1:
        return np.convolve(x, kernel, mode="same")
    return np.stack([np.convolve(x[:, i], kernel, mode="same") for i in range(x.shape[1])], axis=1)


def ppg(hr_per_s: np.ndarray, acc: np.ndarray, kind: str, rng: np.random.Generator,
        dropouts: bool = True) -> np.ndarray:
    """Raw light-sensor counts for this heart rate (one value per second) and these accelerometer samples."""
    n = len(acc)
    t = np.arange(n) / FS
    hr = np.interp(t, np.arange(len(hr_per_s)), hr_per_s)
    hr = hr * (1 + 0.02 * _lowpass(rng.normal(0, 1, n), 1.0) * 10)       # beat-to-beat variation
    phase = np.cumsum(hr / 60.0) / FS
    frac = phase % 1.0
    # One beat: a fast upstroke and a second smaller bump (the dicrotic wave).
    beat = np.exp(-((frac - 0.2) / 0.09) ** 2) + 0.35 * np.exp(-((frac - 0.5) / 0.1) ** 2)
    amp = rng.uniform(12, 30) * (1 + 0.15 * np.sin(2 * np.pi * 0.25 * t))   # breathing changes pulse size
    baseline = rng.uniform(1600, 2600) + 8 * np.sin(2 * np.pi * rng.uniform(0.2, 0.3) * t) \
        + np.cumsum(rng.normal(0, 0.05, n))                                   # slow drift
    signal = baseline - amp * beat              # more blood = less light reaches the sensor

    effort = {"rest": 0.0, "walk": 1.0, "arms": 2.0}[kind]
    dyn = acc - _lowpass(acc, 2.0)               # arm movement only (gravity removed)
    gains = rng.normal(0, 1, 3) * {"rest": 40, "walk": 110, "arms": 120}[kind]
    linear = dyn @ gains
    linear = np.convolve(linear, np.exp(-np.arange(8) / 3.0) / 2.5, mode="same")   # sensor lags the arm a bit
    squared = 60 * np.sum(dyn ** 2, axis=1) * (effort > 0)
    slip = _lowpass(rng.normal(0, 1, n), 0.3) * (2 + 15 * effort)           # skin slip the accelerometer can't see
    signal = signal + linear + squared + slip + rng.normal(0, 1 + 1.5 * effort, n)

    if dropouts and effort > 0:
        i = 0
        while i < n:
            if rng.uniform() < 0.0002 * effort:          # lost the skin for 1-4 s
                d = int(rng.uniform(1, 4) * FS)
                signal[i:i + d] = 4095 if rng.uniform() < 0.5 else rng.uniform(0, 30)
                i += d
            i += 1
    return np.clip(np.round(signal), 0, 4095).astype(int)


def session(kind: str, seconds: int, seed: int) -> dict:
    """One synthetic recording: {"hr": bpm per second, "ppg": counts, "acc": (n,3) g, "cadence_hz": float}."""
    rng = np.random.default_rng(seed)
    hr = hr_trace(kind, seconds, rng)
    acc, f = motion(kind, seconds * FS, rng)
    return {"kind": kind, "hr": hr, "ppg": ppg(hr, acc, kind, rng), "acc": acc, "cadence_hz": f}


def band_lines(rec: dict, t0: float = 0.0) -> list[tuple[float, str]]:
    """
    Turn a session into (laptop time, line) pairs exactly like the band sends them:
    one PPG line (10 values) every 200 ms and one ACC line every 20 ms.
    """
    out = []
    p, a = rec["ppg"], rec["acc"]
    for i in range(len(a)):
        t = t0 + (i + 1) / FS
        out.append((t, f"ACC:{a[i, 0]:.2f},{a[i, 1]:.2f},{a[i, 2]:.2f}"))
        if (i + 1) % 10 == 0:
            out.append((t, "PPG:" + ",".join(str(int(v)) for v in p[i - 9:i + 1])))
    return out


# ---------------------------------------------------------------------------
# Endless stream for the fake band
# ---------------------------------------------------------------------------
class SensorStream:
    """
    Never-ending synthetic PPG + accelerometer + air for the fake band, made one minute at a time.
    hr_at(t) gives the true heart rate at second t (so the fake band's HR: line and its PPG agree).
    The activity cycles through `kinds`, one per minute. fall(t0, seconds) overlays a fall:
    a 3.5 g impact, the wrist ends up at a right angle to before, then perfectly still, and
    the pressure rises 0.1 hPa (the band is ~0.8 m lower).
    """

    BLOCK_S = 60

    def __init__(self, hr_at, seed: int = 0, kinds=("rest", "walk", "arms", "walk"),
                 air_temp_c: float = 31.0, humidity_pct: float = 60.0, pressure_hpa: float = 1012.0):
        self.hr_at = hr_at
        self.rng = np.random.default_rng(seed)
        self.kinds = kinds
        self.air = (air_temp_c, humidity_pct, pressure_hpa)
        self.ppg = np.zeros(0, dtype=int)
        self.acc = np.zeros((0, 3))
        self.fall_at: float | None = None
        self.fall_for = 0.0
        self.fallen_g: np.ndarray | None = None
        self.fall_phase = 0.0

    def _grow(self, n: int) -> None:
        while len(self.ppg) < n:
            block = len(self.ppg) // (self.BLOCK_S * FS)
            kind = self.kinds[block % len(self.kinds)]
            start_s = block * self.BLOCK_S
            hr = np.array([self.hr_at(start_s + s) for s in range(self.BLOCK_S + 1)])
            acc, _ = motion(kind, self.BLOCK_S * FS, self.rng)
            self.ppg = np.concatenate([self.ppg, ppg(hr, acc, kind, self.rng, dropouts=False)])
            self.acc = np.vstack([self.acc, acc])

    def fall(self, t0: float, seconds: float) -> None:
        self.fall_at, self.fall_for, self.fallen_g = t0, seconds, None

    def falling(self, t: float) -> bool:
        return self.fall_at is not None and self.fall_at <= t < self.fall_at + self.fall_for

    def sample(self, i: int) -> tuple[int, np.ndarray]:
        """(PPG count, accelerometer xyz) for sample i (time i / 50 s)."""
        self._grow(i + 1)
        t = i / FS
        p, a = int(self.ppg[i]), self.acc[i]
        if self.falling(t):
            if self.fallen_g is None:
                before = self.acc[max(0, i - FS):i + 1].mean(axis=0)
                side = np.cross(before, [0.0, 0.0, 1.0] if abs(before[2]) < 0.9 else [1.0, 0.0, 0.0])
                self.fallen_g = side / np.linalg.norm(side)        # at a right angle to before
            k = i - int(self.fall_at * FS)
            a = np.array([0.0, 0.0, 3.5]) if k < 3 else self.fallen_g + self.rng.normal(0, 0.003, 3)
            self.fall_phase += self.hr_at(t) / 60 / FS            # beats so far (integrate the heart rate)
            beat = np.exp(-(((self.fall_phase % 1) - 0.2) / 0.09) ** 2)
            p = int(np.clip(round(2000 - 15 * beat + self.rng.normal(0, 1)), 0, 4095))   # lying still: clean pulse
        return p, a

    def air_at(self, t: float) -> tuple[float, float, float]:
        """(air °C, humidity %, pressure hPa) at second t: slow wobble + sensor noise."""
        temp, hum, pres = self.air
        temp += 0.4 * np.sin(2 * np.pi * t / 600) + self.rng.normal(0, 0.03)
        hum += 1.5 * np.sin(2 * np.pi * t / 900) + self.rng.normal(0, 0.2)
        pres += self.rng.normal(0, 0.01) + (0.1 if self.falling(t - 1) else 0.0)
        return temp, hum, pres
