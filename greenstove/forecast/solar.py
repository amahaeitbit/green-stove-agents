"""Clear-sky PV for a site and date, adjusted for forecast cloud cover.

Solar position: NOAA general solar position equations. Clear-sky irradiance: Haurwitz
(GHI = 1098 cos z exp(-0.057 / cos z) W/m2). Cloud adjustment: Kasten-Czeplak
(GHI_cloudy = GHI_clear (1 - 0.75 C^3.4), C = cloud fraction 0..1). PV = kWp x derate x
GHI / 1000 on a horizontal plane. Approximations, documented, not calibrated to a site.
"""
from __future__ import annotations

import math
from datetime import date

STEPS, STEP_HOURS = 96, 0.25


def cos_zenith(lat: float, lon: float, day: date, hour: float, tz_hours: float) -> float:
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = hour * 60 + eqtime + 4 * lon - 60 * tz_hours
    ha = math.radians(tst / 4 - 180)
    phi = math.radians(lat)
    return math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)


def clear_sky_ghi(lat: float, lon: float, day: date, tz_hours: float) -> list[float]:
    out = []
    for s in range(STEPS):
        cz = cos_zenith(lat, lon, day, (s + 0.5) * STEP_HOURS, tz_hours)
        out.append(1098 * cz * math.exp(-0.057 / cz) if cz > 0.01 else 0.0)
    return out


def cloud_factor(cloud_fraction: float) -> float:
    c = min(1.0, max(0.0, cloud_fraction))
    return 1 - 0.75 * c ** 3.4


def pv_profile(lat: float, lon: float, day: date, tz_hours: float, kwp: float, derate: float,
               cloud_by_step: list[float]) -> list[float]:
    ghi = clear_sky_ghi(lat, lon, day, tz_hours)
    return [round(kwp * derate * g / 1000 * cloud_factor(c), 4) for g, c in zip(ghi, cloud_by_step)]


def kwh(profile: list[float]) -> float:
    return round(sum(profile) * STEP_HOURS, 2)
