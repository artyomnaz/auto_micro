"""Load, merge, and validate YAML configuration trees."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from microscope_control.exceptions import ConfigError

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "config" / "default.yaml"

REQUIRED_TOP_LEVEL = ("connection", "axes", "motors")
REQUIRED_AXIS_KEYS = ("min_steps", "max_steps", "default_speed_sps", "default_accel_sps2")


def resolve_config_path(path: str | Path | None = None) -> Path:
    return Path(path) if path else DEFAULT_CONFIG_PATH


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overlay.items():
        if key == "includes":
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Config not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config root must be a mapping: {path}")
    return data


def load_config(path: str | Path | None = None, *, validate: bool = True) -> dict[str, Any]:
    """Load YAML config and optionally merge relative ``includes`` fragments."""
    cfg_path = resolve_config_path(path)
    data = _load_yaml(cfg_path)
    includes = list(data.get("includes") or [])
    merged = {k: v for k, v in data.items() if k != "includes"}
    base_dir = cfg_path.parent
    for rel in includes:
        frag_path = (base_dir / rel).resolve()
        if not frag_path.is_file():
            # Optional fragments: skip missing includes so examples stay portable.
            continue
        frag = _load_yaml(frag_path)
        merged = _deep_merge(merged, frag)
    if validate:
        validate_config(merged)
    return merged


def validate_config(config: dict[str, Any]) -> None:
    for key in REQUIRED_TOP_LEVEL:
        if key not in config:
            raise ConfigError(f"Missing required top-level key: {key}")

    connection = str(config["connection"]).lower()
    if connection not in ("serial", "firmware_runtime", "runtime", "inprocess"):
        raise ConfigError(f"Invalid connection: {connection!r}")

    if connection == "serial":
        serial = config.get("serial") or {}
        if not serial.get("port"):
            raise ConfigError("serial.port is required when connection=serial")

    axes = config["axes"]
    for axis_key in ("F", "M", "I"):
        if axis_key not in axes:
            raise ConfigError(f"Missing axes.{axis_key}")
        axis = axes[axis_key]
        for field in REQUIRED_AXIS_KEYS:
            if field not in axis:
                raise ConfigError(f"Missing axes.{axis_key}.{field}")
        if int(axis["min_steps"]) > int(axis["max_steps"]):
            raise ConfigError(f"axes.{axis_key}: min_steps > max_steps")

    motors = config["motors"]
    if int(motors.get("full_steps_per_rev", 0)) <= 0:
        raise ConfigError("motors.full_steps_per_rev must be > 0")

    micro = config.get("microstepping", {})
    mode = int(micro.get("mode", motors.get("microstep", 16)))
    if mode not in (1, 2, 4, 8, 16, 32, 64, 128, 256):
        raise ConfigError(f"Unsupported microstep mode: {mode}")

    profiles = config.get("motion_profiles") or {}
    default_profile = profiles.get("default")
    if default_profile and default_profile not in profiles:
        # default may be a string key pointing at a profile dict sibling
        if not isinstance(profiles.get(default_profile), dict):
            raise ConfigError(f"Unknown motion profile default: {default_profile!r}")

    presets = config.get("presets") or {}
    for name, preset in presets.items():
        if not isinstance(preset, dict):
            raise ConfigError(f"presets.{name} must be a mapping")
        for axis_key in ("F", "M", "I"):
            if axis_key in preset and not isinstance(preset[axis_key], (int, float)):
                raise ConfigError(f"presets.{name}.{axis_key} must be numeric")


def microstep_mode(config: dict[str, Any]) -> int:
    micro = config.get("microstepping") or {}
    if "mode" in micro:
        return int(micro["mode"])
    return int((config.get("motors") or {}).get("microstep", 16))


def ms_logic_levels(config: dict[str, Any]) -> tuple[int, int, int]:
    mode = str(microstep_mode(config))
    levels = ((config.get("microstepping") or {}).get("ms_levels") or {}).get(mode)
    if not levels or len(levels) != 3:
        raise ConfigError(f"No ms_levels defined for microstep mode {mode}")
    return int(levels[0]), int(levels[1]), int(levels[2])
