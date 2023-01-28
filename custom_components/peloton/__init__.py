"""The Home Assistant Peloton Sensor integration."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

from dateutil import tz
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pylotoncycle import PylotonCycle
from pylotoncycle.pylotoncycle import PelotonLoginException
from requests.exceptions import Timeout

from .const import DOMAIN, STARTUP_MESSAGE
from .sensor import PelotonMetric, PelotonStat, PelotonSummary

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["binary_sensor", "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Home Assistant Peloton Sensor from a config entry."""

    _LOGGER.debug("Loading Peloton integration")

    if DOMAIN not in hass.data:
        # Print startup message
        _LOGGER.info(STARTUP_MESSAGE)

    # Fetch current state object
    _LOGGER.debug("Logging in and setting up session to the Peloton API")
    try:
        api = await hass.async_add_executor_job(
            PylotonCycle, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
        )
    except PelotonLoginException as err:
        _LOGGER.warning("Peloton username or password incorrect")
        raise ConfigEntryAuthFailed from err
    except (ConnectionError, Timeout) as err:
        raise UpdateFailed("Could not connect to Peloton.") from err

    async def async_update_data() -> bool | dict:

        try:
            workouts = await hass.async_add_executor_job(api.GetRecentWorkouts, 1)
            workout_stats_summary = workouts[0]
        except IndexError as err:
            raise UpdateFailed("User has no workouts.") from err
        except (ConnectionError, Timeout) as err:
            raise UpdateFailed("Could not connect to Peloton.") from err

        workout_stats_summary_id = workout_stats_summary["id"]

        return {
            "workout_stats_detail": (
                workout_stats_detail := await hass.async_add_executor_job(
                    api.GetWorkoutMetricsById, workout_stats_summary_id
                )
            ),
            "workout_stats_summary": workout_stats_summary,
            "user_profile": await hass.async_add_executor_job(api.GetMe),
            "quant_data": compile_quant_data(
                workout_stats_summary=workout_stats_summary,
                workout_stats_detail=workout_stats_detail,
            ),
        }

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=10),
    )

    # Fetch initial data so we have data when entities subscribe
    await coordinator.async_config_entry_first_refresh()

    # Load data for domain. If not present, initlaize dict for this domain.
    hass.data.setdefault(DOMAIN, {})

    # Store coordinator
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return bool(unload_ok)


def compile_quant_data(
    workout_stats_summary: dict, workout_stats_detail: dict
) -> list[PelotonStat]:
    """Compiles list of quantative data."""

    # Get Timezone
    user_timezone = (
        tz.gettz(raw_tz)
        if (raw_tz := workout_stats_summary.get("timezone"))
        else tz.gettz("UTC")
    )

    # Preprocess Summaries

    summary: dict
    summaries: dict = {}
    for summary in workout_stats_detail.get("summaries", []):
        if summary.get("slug") == "total_calories" or summary.get("slug") == "calories":
            summaries.update(
                {
                    "total_calories": PelotonSummary(
                        value  # Convert kcal to Wh
                        if isinstance((value := summary.get("value")), int)
                        else None,
                        str(summary.get("display_unit")),
                        None,
                    )
                }
            )
        if summary.get("slug") == "active_calories":
            summaries.update(
                {
                    "active_calories": PelotonSummary(
                        value  # Convert kcal to Wh
                        if isinstance((value := summary.get("value")), int)
                        else None,
                        str(summary.get("display_unit")),
                        None,
                    )
                }
            )
        if summary.get("slug") == "distance":
            summaries.update(
                {
                    "distance": PelotonSummary(
                        value
                        if isinstance((value := summary.get("value")), float)
                        else None,
                        str(summary.get("display_unit")),
                        None,
                    )
                }
            )
        if summary.get("slug") == "total_output":
            summaries.update(
                {
                    "totaloutput": PelotonSummary(
                        value
                        if isinstance((value := summary.get("value")), int)
                        else None,
                        str(summary.get("display_unit")),
                        None,
                    )
                }
            )

    # Preprocess Metrics

    metric: dict
    metrics: dict = {}
    for metric in workout_stats_detail.get("metrics", []):
        if metric.get("slug") == "heart_rate":
            metrics.update(
                {
                    "heart_rate": PelotonMetric(
                        int(max_val)
                        if isinstance((max_val := metric.get("max_value")), int)
                        else None,
                        int(avg_val)
                        if isinstance((avg_val := metric.get("average_value")), int)
                        else None,
                        int(val)
                        if isinstance((val := metric.get("values")[len(metric.get("values"))-1]), int)
                        else None,
                        str(metric.get("display_unit")),
                        None,
                    )
                }
            )
        if metric.get("slug") == "resistance":
            metrics.update(
                {
                    "resistance": PelotonMetric(
                        int(max_val)
                        if isinstance((max_val := metric.get("max_value")), int)
                        else None,
                        int(avg)
                        if isinstance((avg := metric.get("average_value")), int)
                        else None,
                        int(value)
                        if isinstance((value := metric.get("values")[len(metric.get("values"))-1]), int)
                        else None,
                        "%",
                        None,
                    )
                }
            )
        if metric.get("slug") == "speed":
            metrics.update(
                {
                    "speed": PelotonMetric(
                        max_val
                        if isinstance((max_val := metric.get("max_value")), float)
                        else None,
                        avg
                        if isinstance((avg := metric.get("average_value")), float)
                        else None,
                       value
                        if isinstance((value := metric.get("values")[len(metric.get("values"))-1]), float)
                        else None,
                        str(metric.get("display_unit")),
                        None,
                    )
                }
            )
        if metric.get("slug") == "cadence":
            metrics.update(
                {
                    "cadence": PelotonMetric(
                        int(max_val)
                        if isinstance((max_val := metric.get("max_value")), int)
                        else None,
                        int(avg)
                        if isinstance((avg := metric.get("average_value")), int)
                        else None,
                        int(value)
                        if isinstance((value := metric.get("values")[len(metric.get("values"))-1]), int)
                        else None,
                        "rpm",
                        None,
                    )
                }
            )
        if metric.get("slug") == "output":
            metrics.update(
                {
                    "output": PelotonMetric(
                        int(max_val)
                        if isinstance((max_val := metric.get("max_value")), int)
                        else None,
                        int(avg)
                        if isinstance((avg := metric.get("average_value")), int)
                        else None,
                        int(value)
                        if isinstance((value := metric.get("values")[len(metric.get("values"))-1]), int)
                        else None,
                        "W",
                        SensorDeviceClass.POWER,
                    )
                }
            )

    # Build and return list.

    return [
        PelotonStat(
            "Start Time",
            datetime.fromtimestamp(workout_stats_summary["start_time"], user_timezone)
            if "start_time" in workout_stats_summary
            and workout_stats_summary["start_time"] is not None
            else None,
            None,
            SensorDeviceClass.TIMESTAMP,
            None,
            "mdi:timer-sand",
        ),
        PelotonStat(
            "End Time",
            datetime.fromtimestamp(workout_stats_summary["end_time"], user_timezone)
            if "end_time" in workout_stats_summary
            and workout_stats_summary["end_time"] is not None or not 'null'
            else datetime.fromtimestamp(workout_stats_summary["start_time"], user_timezone),
            None,
            SensorDeviceClass.TIMESTAMP,
            None,
            "mdi:timer-sand-complete",
        ),
        PelotonStat(
            "Duration",
            round((duration_sec / 60), 2)
            if (
                (duration_sec := workout_stats_summary.get("ride", {}).get("duration"))
                and duration_sec is not None
            )
            else None,
            UnitOfTime.MINUTES,
            None,
            SensorStateClass.MEASUREMENT,
            "mdi:timer-outline",
        ),
        PelotonStat(
            "Leaderboard: Rank",
            workout_stats_summary.get("leaderboard_rank", 0),
            None,
            None,
            SensorStateClass.MEASUREMENT,
            "mdi:trophy-award",
        ),
        PelotonStat(
            "Leaderboard: Total Users",
            workout_stats_summary.get("total_leaderboard_users", 0),
            None,
            None,
            SensorStateClass.MEASUREMENT,
            "mdi:account-group",
        ),
        PelotonStat(
            "Power Output",
            round(total_work / 3600, 4)  # Converts joules to Wh
            if "total_work" in workout_stats_summary
            and isinstance(total_work := workout_stats_summary.get("total_work"), float)
            else None,
            UnitOfEnergy.WATT_HOUR,
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL,
            None,
        ),
        PelotonStat(
            "Distance",
            getattr(summaries.get("distance"), "total", None),
            getattr(summaries.get("distance"), "unit", None),
            getattr(summaries.get("distance"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:map-marker-distance",
        ),
        PelotonStat(
            "Total Calories",
            getattr(summaries.get("total_calories"), "total", None),
            getattr(summaries.get("total_calories"), "unit", None),
            getattr(summaries.get("total_calories"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:fire",
        ),
        PelotonStat(
            "Active Calories",
            getattr(summaries.get("active_calories"), "total", None),
            getattr(summaries.get("active_calories"), "unit", None),
            getattr(summaries.get("active_calories"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:fire",
        ),
        PelotonStat(
            "Total Output",
            getattr(summaries.get("totaloutput"), "total", None),
            getattr(summaries.get("totaloutput"), "unit", None),
            getattr(summaries.get("totaloutput"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:lightning-bolt",
        ),
        PelotonStat(
            "Heart Rate: Average",
            getattr(metrics.get("heart_rate"), "avg_val", None),
            getattr(metrics.get("heart_rate"), "unit", None),
            getattr(metrics.get("heart_rate"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:heart-pulse",
        ),
        PelotonStat(
            "Heart Rate: Max",
            getattr(metrics.get("heart_rate"), "max_val", None),
            getattr(metrics.get("heart_rate"), "unit", None),
            getattr(metrics.get("heart_rate"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:heart-pulse",
        ),
        PelotonStat(
            "Heart Rate: Current",
            getattr(metrics.get("heart_rate"), "value", None),
            getattr(metrics.get("heart_rate"), "unit", None),
            getattr(metrics.get("heart_rate"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:heart-pulse",
        ),
        PelotonStat(
            "Resistance: Average",
            getattr(metrics.get("resistance"), "avg_val", None),
            getattr(metrics.get("resistance"), "unit", None),
            getattr(metrics.get("resistance"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:network-strength-2",
        ),
        PelotonStat(
            "Resistance: Max",
            getattr(metrics.get("resistance"), "max_val", None),
            getattr(metrics.get("resistance"), "unit", None),
            getattr(metrics.get("resistance"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:network-strength-4",
        ),
        PelotonStat(
            "Resistance: Current",
            getattr(metrics.get("resistance"), "value", None),
            getattr(metrics.get("resistance"), "unit", None),
            getattr(metrics.get("resistance"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:network-strength-1",
        ),
        PelotonStat(
            "Speed: Average",
            getattr(metrics.get("speed"), "avg_val", None),
            getattr(metrics.get("speed"), "unit", None),
            getattr(metrics.get("speed"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:speedometer-medium",
        ),
        PelotonStat(
            "Speed: Max",
            getattr(metrics.get("speed"), "max_val", None),
            getattr(metrics.get("speed"), "unit", None),
            getattr(metrics.get("speed"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:speedometer",
        ),
        PelotonStat(
            "Speed: Current",
            getattr(metrics.get("speed"), "value", None),
            getattr(metrics.get("speed"), "unit", None),
            getattr(metrics.get("speed"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:speedometer-slow",
        ),
        PelotonStat(
            "Cadence: Average",
            getattr(metrics.get("cadence"), "avg_val", None),
            getattr(metrics.get("cadence"), "unit", None),
            getattr(metrics.get("cadence"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:fan",
        ),
        PelotonStat(
            "Cadence: Max",
            getattr(metrics.get("cadence"), "max_val", None),
            getattr(metrics.get("cadence"), "unit", None),
            getattr(metrics.get("cadence"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:fan-chevron-up",
        ),
        PelotonStat(
            "Cadence: Current",
            getattr(metrics.get("cadence"), "value", None),
            getattr(metrics.get("cadence"), "unit", None),
            getattr(metrics.get("cadence"), "device_class", None),
            SensorStateClass.MEASUREMENT,
            "mdi:fan-clock",
        ),
    ]
