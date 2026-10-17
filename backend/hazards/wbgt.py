"""
Outdoor Wet Bulb Globe Temperature (WBGT) with the Liljegren method.

WBGT is the heat-stress number used for outdoor work rules. It mixes air
temperature, humidity, wind and sun:
    WBGT = 0.7 * natural wet bulb + 0.2 * black globe + 0.1 * air temperature

We can't measure the wet bulb or the globe, so we predict both from normal
weather data by solving their heat balance (Liljegren et al., 2008):
- the black globe heats up in the sun and is cooled by wind;
- the wet wick cools by evaporation, more when the air is dry and windy.

This is a hand-written Python port of Liljegren's wbgt.c (Argonne National
Laboratory, version 1.1), checked line by line against the original. It is
vectorized with numpy: pass single numbers or whole arrays.

    from backend.hazards.wbgt import wbgt_liljegren, wbgt_from_weather
    out = wbgt_liljegren(tair_c=33, rh_pct=55, pres_hpa=1013, wind_ms=2,
                         solar_wm2=850, cza=0.9)
    out["wbgt"]  # °C

    # From an hourly weather row (Open-Meteo style), sun position computed for you:
    wbgt_from_weather(time_utc, lat, lon, tair_c, rh_pct, pres_hpa, wind_ms_10m, solar_wm2)

Reference: Liljegren, J. C., Carhart, R. A., Lawday, P., Tschopp, S., Sharp, R.
"Modeling the Wet Bulb Globe Temperature Using Standard Meteorological
Measurements." J. Occup. Environ. Hyg. 5:10, 645-655 (2008).
"This product includes software produced by UChicago Argonne, LLC under
Contract No. DE-AC02-06CH11357 with the Department of Energy."
(Ported to Python for PlanIT Control, 2026.)
"""

import math
from datetime import datetime, timedelta, timezone

import numpy as np

from backend.config import WBGT_BLACK_C, WBGT_GREEN_C, WBGT_RED_C, WBGT_YELLOW_C

# ---------------------------------------------------------------------------
# Physical constants (same values as wbgt.h)
# ---------------------------------------------------------------------------
SOLAR_CONST = 1367.0        # W/m², sunlight at the top of the atmosphere
STEFANB = 5.6696e-8         # Stefan-Boltzmann constant
CP = 1003.5                 # specific heat of air, J/(kg K)
M_AIR = 28.97               # molar mass of air
M_H2O = 18.015              # molar mass of water
RATIO = CP * M_AIR / M_H2O
R_GAS = 8314.34
R_AIR = R_GAS / M_AIR
PR = CP / (CP + 1.25 * R_AIR)   # Prandtl number

# The wet wick (natural wet bulb thermometer)
EMIS_WICK, ALB_WICK, D_WICK, L_WICK = 0.95, 0.4, 0.007, 0.0254
# The black globe (standard 2-inch globe)
EMIS_GLOBE, ALB_GLOBE, D_GLOBE = 0.95, 0.05, 0.0508
# The ground
EMIS_SFC, ALB_SFC = 0.999, 0.45

# Limits used by the original code
CZA_MIN = 0.00873           # sun below ~89.5° zenith counts as down
NORMSOLAR_MAX = 0.85        # cap on sunlight as a fraction of top-of-atmosphere
REF_HEIGHT = 2.0            # WBGT is defined with wind at 2 m
MIN_SPEED = 0.13            # m/s, the slowest wind the formulas allow
CONVERGENCE = 0.02          # K, stop iterating when the answer moves less than this
MAX_ITER = 500


# ---------------------------------------------------------------------------
# Air and water properties (temperatures in kelvin, pressure in hPa = mb)
# ---------------------------------------------------------------------------
def esat(tk):
    """Saturation vapor pressure over water, hPa (Buck 1981, with moist-air factor)."""
    y = (tk - 273.15) / (tk - 32.18)
    return 1.004 * 6.1121 * np.exp(17.502 * y)


def dew_point(e):
    """Dew point (K) from vapor pressure e (hPa)."""
    z = np.log(e / (6.1121 * 1.004))
    return 273.15 + 240.97 * z / (17.502 - z)


def viscosity(tk):
    """Viscosity of air, kg/(m s)."""
    sigma, eps_kappa = 3.617, 97.0
    tr = tk / eps_kappa
    omega = (tr - 2.9) / 0.4 * (-0.034) + 1.048
    return 2.6693e-6 * np.sqrt(M_AIR * tk) / (sigma * sigma * omega)


def thermal_cond(tk):
    """Thermal conductivity of air, W/(m K)."""
    return (CP + 1.25 * R_AIR) * viscosity(tk)


def diffusivity(tk, pres):
    """Diffusivity of water vapor in air, m²/s."""
    pcrit13 = (36.4 * 218.0) ** (1.0 / 3.0)
    tcrit512 = (132.0 * 647.3) ** (5.0 / 12.0)
    tcrit12 = math.sqrt(132.0 * 647.3)
    mmix = math.sqrt(1.0 / M_AIR + 1.0 / M_H2O)
    patm = pres / 1013.25
    return 3.640e-4 * (tk / tcrit12) ** 2.334 * pcrit13 * tcrit512 * mmix / patm * 1e-4


def evap(tk):
    """Heat of evaporation of water, J/kg."""
    return (313.15 - tk) / 30.0 * (-71100.0) + 2.4073e6


def emis_atm(tk, rh):
    """How strongly the sky radiates heat back down (rh as a fraction)."""
    return 0.575 * (rh * esat(tk)) ** 0.143


def h_sphere_in_air(diameter, tk, pres, speed):
    """Convective heat transfer for the globe, W/(m² K)."""
    density = pres * 100.0 / (R_AIR * tk)
    re = np.maximum(speed, MIN_SPEED) * density * diameter / viscosity(tk)
    nu = 2.0 + 0.6 * np.sqrt(re) * PR ** 0.3333
    return nu * thermal_cond(tk) / diameter


def h_cylinder_in_air(diameter, tk, pres, speed):
    """Convective heat transfer for the wick (a cylinder in cross flow), W/(m² K)."""
    a, b, c = 0.56, 0.281, 0.4
    density = pres * 100.0 / (R_AIR * tk)
    re = np.maximum(speed, MIN_SPEED) * density * diameter / viscosity(tk)
    nu = b * re ** (1.0 - c) * PR ** (1.0 - a)
    return nu * thermal_cond(tk) / diameter


# ---------------------------------------------------------------------------
# The two iterative solves
# ---------------------------------------------------------------------------
def _iterate(step, first_guess):
    """Run the damped fixed-point loop from wbgt.c on whole arrays.

    step(prev) returns the new estimate. Each element stops once it moves
    less than CONVERGENCE; elements that never converge come back NaN.
    """
    prev = first_guess.copy()
    result = np.full_like(prev, np.nan)
    active = np.ones(prev.shape, dtype=bool)
    for _ in range(MAX_ITER):
        new = step(prev)
        done = active & (np.abs(new - prev) < CONVERGENCE)
        result[done] = new[done]
        active &= ~done
        if not active.any():
            break
        prev = np.where(active, 0.9 * prev + 0.1 * new, prev)
    return result


def globe_temp(tk, rh, pres, speed, solar, fdir, cza):
    """Black globe temperature (K)."""
    tsfc = tk
    sky = 0.5 * (emis_atm(tk, rh) * tk ** 4 + EMIS_SFC * tsfc ** 4)
    # Sun term; guard cza so the night-time (fdir = 0) case never divides by zero
    safe_cza = np.where(cza > CZA_MIN, cza, 1.0)
    sun = solar / (2.0 * STEFANB * EMIS_GLOBE) * (1.0 - ALB_GLOBE) * \
        (fdir * (1.0 / (2.0 * safe_cza) - 1.0) + 1.0 + ALB_SFC)

    def step(tg_prev):
        tref = 0.5 * (tg_prev + tk)
        h = h_sphere_in_air(D_GLOBE, tref, pres, speed)
        return (sky - h / (STEFANB * EMIS_GLOBE) * (tg_prev - tk) + sun) ** 0.25

    return _iterate(step, tk)


def wet_bulb_temp(tk, rh, pres, speed, solar, fdir, cza, radiation=True):
    """Natural wet bulb (radiation=True) or psychrometric wet bulb (False), in K."""
    tsfc = tk
    sza = np.arccos(np.clip(cza, -1.0, 1.0))
    eair = rh * esat(tk)
    tdew = dew_point(eair)
    rad = 1.0 if radiation else 0.0
    # tan(sza) blows up at the horizon, but fdir is 0 there, so the term is unused
    tan_term = np.where(cza > CZA_MIN, np.tan(sza) / math.pi, 0.0)
    sun = (1.0 - ALB_WICK) * solar * (
        (1.0 - fdir) * (1.0 + 0.25 * D_WICK / L_WICK)
        + fdir * (tan_term + 0.25 * D_WICK / L_WICK) + ALB_SFC)
    sky = 0.5 * (emis_atm(tk, rh) * tk ** 4 + EMIS_SFC * tsfc ** 4)

    def step(twb_prev):
        tref = 0.5 * (twb_prev + tk)
        h = h_cylinder_in_air(D_WICK, tref, pres, speed)
        fatm = STEFANB * EMIS_WICK * (sky - twb_prev ** 4) + sun
        ewick = esat(twb_prev)
        density = pres * 100.0 / (R_AIR * tref)
        sc = viscosity(tref) / (density * diffusivity(tref, pres))
        return (tk - evap(tref) / RATIO * (ewick - eair) / (pres - ewick) * (PR / sc) ** 0.56
                + fatm / h * rad)

    return _iterate(step, tdew)


# ---------------------------------------------------------------------------
# Sun and wind helpers
# ---------------------------------------------------------------------------
def solar_params(solar, cza, earth_sun_au=1.0):
    """Cap sunlight at what's physically possible and estimate the direct-beam fraction.

    Returns (solar, fdir). Same rules as calc_solar_parameters in wbgt.c.
    """
    solar = np.asarray(solar, dtype=float)
    cza = np.asarray(cza, dtype=float)
    toa = SOLAR_CONST * np.maximum(0.0, cza) / (earth_sun_au ** 2)
    toa = np.where(cza < CZA_MIN, 0.0, toa)
    with np.errstate(divide="ignore", invalid="ignore"):
        norm = np.where(toa > 0, np.minimum(solar / toa, NORMSOLAR_MAX), 0.0)
        fdir = np.where(norm > 0, np.exp(3.0 - 1.34 * norm - 1.65 / norm), 0.0)
    fdir = np.clip(fdir, 0.0, 0.9)
    solar = np.where(toa > 0, norm * toa, 0.0)
    return solar, fdir


def stability_class(daytime, speed, solar, dT=0.0):
    """Atmospheric stability class 1-6 (EPA solar radiation / delta-T method)."""
    table = [[1, 1, 2, 4, 0, 5, 6, 0],
             [1, 2, 3, 4, 0, 5, 6, 0],
             [2, 2, 3, 4, 0, 4, 4, 0],
             [3, 3, 4, 4, 0, 0, 0, 0],
             [3, 4, 4, 4, 0, 0, 0, 0],
             [0, 0, 0, 0, 0, 0, 0, 0]]
    if daytime:
        j = 0 if solar >= 925 else 1 if solar >= 675 else 2 if solar >= 175 else 3
        i = 4 if speed >= 6 else 3 if speed >= 5 else 2 if speed >= 3 else 1 if speed >= 2 else 0
    else:
        j = 6 if dT >= 0 else 5
        i = 2 if speed >= 2.5 else 1 if speed >= 2.0 else 0
    return table[i][j]


def wind_at_2m(speed, height_m, daytime, solar, urban=False, dT=0.0):
    """Estimate 2 m wind from wind measured at another height (power law)."""
    if height_m == REF_HEIGHT:
        return max(speed, MIN_SPEED)
    urban_exp = (0.15, 0.15, 0.20, 0.25, 0.30, 0.30)
    rural_exp = (0.07, 0.07, 0.10, 0.15, 0.35, 0.55)
    cls = stability_class(daytime, speed, solar, dT)
    exponent = (urban_exp if urban else rural_exp)[cls - 1]
    return max(speed * (REF_HEIGHT / height_m) ** exponent, MIN_SPEED)


def cos_zenith(when_utc, lat, lon):
    """Cosine of the sun's zenith angle (NOAA solar position equations).

    1 = sun straight overhead, 0 = on the horizon, negative = night.
    """
    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=timezone.utc)
    when_utc = when_utc.astimezone(timezone.utc)
    doy = when_utc.timetuple().tm_yday
    hour = when_utc.hour + when_utc.minute / 60 + when_utc.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)       # fractional year, radians
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    true_solar_min = hour * 60 + eqtime + 4 * lon
    hour_angle = math.radians(true_solar_min / 4 - 180)
    lat_r = math.radians(lat)
    return (math.sin(lat_r) * math.sin(decl)
            + math.cos(lat_r) * math.cos(decl) * math.cos(hour_angle))


def earth_sun_distance_au(when_utc):
    doy = when_utc.timetuple().tm_yday
    return 1.0 - 0.01672 * math.cos(math.radians(0.9856 * (doy - 4)))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def wbgt_liljegren(tair_c, rh_pct, pres_hpa, wind_ms, solar_wm2, cza, fdir=None,
                   earth_sun_au=1.0):
    """WBGT and its parts, from weather with wind already at 2 m.

    Inputs can be numbers or numpy arrays of the same shape:
      tair_c     air temperature, °C
      rh_pct     relative humidity, %
      pres_hpa   air pressure, hPa
      wind_ms    wind speed at 2 m, m/s
      solar_wm2  global horizontal solar radiation, W/m²
      cza        cosine of the solar zenith angle (see cos_zenith)
      fdir       fraction of sunlight that is direct beam; None = estimate it
    Returns a dict of numpy arrays (or floats): wbgt, tg (globe), tnwb
    (natural wet bulb), tpsy (psychrometric wet bulb), all °C, plus fdir.
    """
    tk = np.asarray(tair_c, dtype=float) + 273.15
    rh = np.asarray(rh_pct, dtype=float) / 100.0
    pres = np.asarray(pres_hpa, dtype=float)
    speed = np.asarray(wind_ms, dtype=float)
    cza = np.asarray(cza, dtype=float)
    solar, est_fdir = solar_params(solar_wm2, cza, earth_sun_au)
    if fdir is not None:
        fdir = np.where(cza >= CZA_MIN, np.clip(np.asarray(fdir, dtype=float), 0, 1), 0.0)
    else:
        fdir = est_fdir
    tk, rh, pres, speed, solar, fdir, cza = np.broadcast_arrays(tk, rh, pres, speed, solar, fdir, cza)
    tg = globe_temp(tk, rh, pres, speed, solar, fdir, cza) - 273.15
    tnwb = wet_bulb_temp(tk, rh, pres, speed, solar, fdir, cza, radiation=True) - 273.15
    tpsy = wet_bulb_temp(tk, rh, pres, speed, solar, fdir, cza, radiation=False) - 273.15
    wbgt = 0.1 * (tk - 273.15) + 0.2 * tg + 0.7 * tnwb
    out = {"wbgt": wbgt, "tg": tg, "tnwb": tnwb, "tpsy": tpsy, "fdir": fdir}
    if wbgt.ndim == 0:
        out = {k: float(v) for k, v in out.items()}
    return out


def wbgt_from_weather(time_utc, lat, lon, tair_c, rh_pct, pres_hpa, wind_ms, solar_wm2,
                      wind_height_m=10.0, solar_avg_minutes=60, urban=False):
    """WBGT for one weather reading at a place and time.

    Works out the sun angle for you and moves the wind from wind_height_m
    (Open-Meteo gives 10 m) down to 2 m. Open-Meteo's solar radiation is the
    average over the PREVIOUS hour, so the sun angle is taken at the middle
    of that hour (solar_avg_minutes / 2 earlier), like wbgt.c does.
    Returns the same dict as wbgt_liljegren, plus wind_2m and cza.
    """
    mid = time_utc - timedelta(minutes=solar_avg_minutes / 2)
    cza = cos_zenith(mid, lat, lon)
    daytime = cza > 0
    wind2 = wind_at_2m(wind_ms, wind_height_m, daytime, solar_wm2, urban=urban)
    out = wbgt_liljegren(tair_c, rh_pct, pres_hpa, wind2, solar_wm2, cza,
                         earth_sun_au=earth_sun_distance_au(mid))
    out["wind_2m"] = wind2
    out["cza"] = cza
    return out


def wbgt_flag(wbgt_c):
    """Heat flag for a WBGT in °C: 'none', 'green', 'yellow', 'red' or 'black'."""
    if wbgt_c is None or (isinstance(wbgt_c, float) and math.isnan(wbgt_c)):
        return None
    if wbgt_c >= WBGT_BLACK_C:
        return "black"
    if wbgt_c >= WBGT_RED_C:
        return "red"
    if wbgt_c >= WBGT_YELLOW_C:
        return "yellow"
    if wbgt_c >= WBGT_GREEN_C:
        return "green"
    return "none"


# Hazard severity for each flag (used by the site hazard picture)
FLAG_SEVERITY = {"green": "info", "yellow": "advisory", "red": "warning", "black": "danger"}


__all__ = ["wbgt_liljegren", "wbgt_from_weather", "wbgt_flag", "cos_zenith", "FLAG_SEVERITY",
           "datetime"]
