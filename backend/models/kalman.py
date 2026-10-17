"""
ECTemp: the U.S. Army's Kalman filter for core temperature (Buller et al., 2013).

Idea: core temperature (CT) is hidden. Heart rate (HR) rises with CT, so each
1-minute average HR is a noisy "reading" of CT. Every minute we:
  1. Predict: CT stays the same, but we get a bit less sure (variance grows).
  2. Observe: compare the real HR with the HR this CT should produce.
  3. Update: nudge CT toward whatever explains the real HR, and get more sure.

The ECTemp method is patented. Fine for a hackathon; see PROJECT.md before
turning this into a product.
"""

from dataclasses import dataclass

import numpy as np

from backend import config


@dataclass
class KalmanState:
    ct: float   # core temperature estimate, °C
    v: float    # variance of that estimate (uncertainty), °C^2


def expected_hr(ct: float) -> float:
    """The heart rate this core temp should produce: h(CT) = b2*CT^2 + b1*CT + b0."""
    return config.KALMAN_B2 * ct**2 + config.KALMAN_B1 * ct + config.KALMAN_B0


class ECTempKalman:
    """Keeps one worker's core temp estimate and updates it once per minute."""

    def __init__(self, ct_start: float = config.KALMAN_CT_START, v_start: float = config.KALMAN_V_START):
        self.state = KalmanState(ct=ct_start, v=v_start)

    def step(self, hr: float | None) -> KalmanState:
        """
        Advance one minute. `hr` is that minute's average heart rate.
        If there was no heart rate that minute (watch dropped out), we only
        predict: the estimate stays put and the uncertainty grows.
        """
        gamma_sq = config.KALMAN_GAMMA**2
        sigma_sq = config.KALMAN_SIGMA**2
        ct_prev, v_prev = self.state.ct, self.state.v

        # 1. Predict: CT- = CT_prev,  v- = v_prev + gamma^2
        ct_pred = ct_prev
        v_pred = v_prev + gamma_sq

        if hr is None:
            self.state = KalmanState(ct=ct_pred, v=v_pred)
            return self.state

        # 2. Observe: slope of h at the prediction, c = 2*b2*CT- + b1
        c = 2 * config.KALMAN_B2 * ct_pred + config.KALMAN_B1

        # 3. Update:
        #    K  = v- * c / (c^2 * v- + sigma^2)
        #    CT = CT- + K * (HR - h(CT-))
        #    v  = (1 - K*c) * v-
        k = v_pred * c / (c**2 * v_pred + sigma_sq)
        ct = ct_pred + k * (hr - expected_hr(ct_pred))
        v = (1 - k * c) * v_pred

        self.state = KalmanState(ct=ct, v=v)
        return self.state


    def step_fused(self, readings: list[tuple[float | None, float]]) -> KalmanState:
        """
        Sensor fusion: advance one minute using SEVERAL heart rate sources at once.

        readings: [(heart rate or None, sigma), ...], one per source. sigma is how noisy
        that source is (bpm): the published value for a watch, bigger for a band pulse
        sensor. Each available reading nudges the estimate in turn (a sequential Kalman
        update), so a noisy source counts less and a dropped source simply skips.
        With one source at the published sigma this is exactly ECTemp.
        """
        gamma_sq = config.KALMAN_GAMMA**2
        ct, v = self.state.ct, self.state.v + gamma_sq        # predict once per minute
        for hr, sigma in readings:
            if hr is None or (isinstance(hr, float) and np.isnan(hr)):
                continue
            c = 2 * config.KALMAN_B2 * ct + config.KALMAN_B1
            k = v * c / (c**2 * v + sigma**2)
            ct = ct + k * (hr - expected_hr(ct))
            v = (1 - k * c) * v
        self.state = KalmanState(ct=ct, v=v)
        return self.state


def run_kalman(hr_per_minute, ct_start: float = config.KALMAN_CT_START,
               v_start: float = config.KALMAN_V_START) -> tuple[np.ndarray, np.ndarray]:
    """
    Run the filter over a whole series of 1-minute heart rates (NaN or None = missing).
    Returns two arrays: core temp estimates and their variances, one per minute.
    Handy for training data and evaluation.
    """
    kf = ECTempKalman(ct_start, v_start)
    cts, vs = [], []
    for hr in hr_per_minute:
        missing = hr is None or (isinstance(hr, float) and np.isnan(hr))
        s = kf.step(None if missing else float(hr))
        cts.append(s.ct)
        vs.append(s.v)
    return np.array(cts), np.array(vs)
