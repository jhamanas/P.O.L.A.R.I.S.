"""Synthetic weather generator for Antarctic stations.

Produces deterministic (seeded) weather with:
  - Sinusoidal annual temperature cycle (coldest in July for Southern Hemisphere)
  - AR(1) noise for day-to-day persistence
  - Poisson-arrival storm events with sustained duration
  - Solar irradiance driven by season and time-of-day with polar night
  - Wind correlated with temperature (katabatic flow)
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .config import param_value
from .types import Environment


class WeatherGenerator:
    """Deterministic weather generator for one station.

    All randomness flows through a single ``numpy.random.Generator``
    seeded at construction, so identical seeds produce identical sequences.
    """

    def __init__(self, station_weather: dict[str, Any], params: dict[str, Any],
                 seed: int = 42):
        self.rng = np.random.default_rng(seed)

        # Station-specific climate
        self.winter_temp = param_value(station_weather, "winter_temp_avg")
        self.summer_temp = param_value(station_weather, "summer_temp_avg")
        self.avg_wind = param_value(station_weather, "avg_wind")
        self.solar_peak = (
            param_value(station_weather, "solar_peak")
            if "solar_peak" in station_weather
            else 800.0
        )

        # AR(1) parameters from global config
        self.temp_rho = param_value(params, "weather_ar1_temp_rho")
        self.temp_sigma = param_value(params, "weather_ar1_temp_sigma")
        self.wind_rho = param_value(params, "weather_ar1_wind_rho")
        self.wind_sigma = param_value(params, "weather_ar1_wind_sigma")

        # Storm parameters
        self.storm_prob = param_value(params, "weather_storm_prob_per_hour")
        self.storm_temp_drop = param_value(params, "weather_storm_temp_drop")
        self.storm_wind_boost = param_value(params, "weather_storm_wind_boost")
        self.storm_min_duration = param_value(params, "weather_storm_min_duration")

        # Internal AR(1) state
        self._temp_noise: float = 0.0
        self._wind_noise: float = 0.0

        # Storm state: remaining hours of current storm (0 = no storm)
        self._storm_remaining: float = 0.0

    def get_weather(self, day_of_year: float, dt_hours: float = 1.0) -> Environment:
        """Generate weather for one timestep.

        Args:
            day_of_year: Fractional day of year (0-365).
            dt_hours: Timestep length in hours.

        Returns:
            Frozen Environment snapshot.
        """
        # --- AR(1) noise (adjusted for dt_hours) ---
        rho_temp_dt = self.temp_rho ** dt_hours
        self._temp_noise = (
            rho_temp_dt * self._temp_noise
            + self.rng.normal(0, self.temp_sigma) * np.sqrt(1 - rho_temp_dt ** 2)
        )
        
        rho_wind_dt = self.wind_rho ** dt_hours
        self._wind_noise = (
            rho_wind_dt * self._wind_noise
            + self.rng.normal(0, self.wind_sigma) * np.sqrt(1 - rho_wind_dt ** 2)
        )

        # --- Annual temperature cycle ---
        # Coldest around day 190 (mid-July) for Southern Hemisphere
        seasonal = -np.cos(2 * np.pi * (day_of_year - 190) / 365.25)
        temp_amplitude = (self.summer_temp - self.winter_temp) / 2.0
        temp_mean = (self.summer_temp + self.winter_temp) / 2.0
        base_temp = temp_mean + temp_amplitude * seasonal

        # Diurnal cycle (small, ±2°C in summer, negligible in winter)
        hour = (day_of_year % 1.0) * 24.0
        diurnal_amplitude = max(0.0, 2.0 * (1 + seasonal))  # 0 in winter, ~4 in summer
        diurnal = diurnal_amplitude * np.sin(2 * np.pi * (hour - 6) / 24.0)

        temperature = base_temp + self._temp_noise + diurnal

        # --- Storm logic ---
        if self._storm_remaining <= 0:
            # Check for new storm arrival (Poisson process)
            if self.rng.random() < self.storm_prob * dt_hours:
                # Storm duration: min_duration + exponential extra
                self._storm_remaining = (
                    self.storm_min_duration
                    + self.rng.exponential(12.0)  # mean 12h extra
                )
        else:
            self._storm_remaining -= dt_hours

        is_storm = self._storm_remaining > 0

        # --- Wind ---
        wind_speed = self.avg_wind + self._wind_noise
        if is_storm:
            wind_speed += self.storm_wind_boost
            temperature -= self.storm_temp_drop
        wind_speed = max(0.0, wind_speed)

        # --- Solar irradiance ---
        # Polar night when sun doesn't rise; simplified model
        # Declination-based day length approximation for ~70°S
        declination = -23.44 * np.cos(2 * np.pi * (day_of_year + 10) / 365.25)
        lat_rad = np.radians(-70.0)
        decl_rad = np.radians(declination)
        cos_hour_angle = -np.tan(lat_rad) * np.tan(decl_rad)

        if cos_hour_angle <= -1:
            # Polar day (midnight sun)
            daylight_fraction = 1.0
        elif cos_hour_angle >= 1:
            # Polar night
            daylight_fraction = 0.0
        else:
            daylight_fraction = np.arccos(cos_hour_angle) / np.pi

        # Solar follows a sinusoidal curve during daylight hours
        sunrise = 12.0 - 12.0 * daylight_fraction
        sunset = 12.0 + 12.0 * daylight_fraction
        if sunrise < hour < sunset and daylight_fraction > 0:
            solar_angle = np.pi * (hour - sunrise) / (sunset - sunrise)
            solar = self.solar_peak * np.sin(solar_angle) * (0.5 + 0.5 * seasonal)
        else:
            solar = 0.0
        solar = max(0.0, solar)

        return Environment(
            temperature=temperature,
            wind_speed=wind_speed,
            solar_irradiance=solar,
            is_storm=is_storm,
            day_of_year=day_of_year,
            hour=hour,
        )
