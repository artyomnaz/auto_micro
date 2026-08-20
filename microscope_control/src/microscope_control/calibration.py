"""Geometric calibration and unit conversions for the three axes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from microscope_control.axes import Axis
from microscope_control.config import microstep_mode
from microscope_control.exceptions import CalibrationError


@dataclass(frozen=True)
class FocusCalibration:
    leadscrew_pitch_mm: float
    gear_ratio: float
    full_steps_per_rev: int
    microstep: int

    @property
    def pitch_um(self) -> float:
        return self.leadscrew_pitch_mm * 1000.0

    @property
    def microsteps_per_rev(self) -> float:
        return self.full_steps_per_rev * self.microstep * self.gear_ratio

    @property
    def um_per_microstep(self) -> float:
        if self.microsteps_per_rev <= 0:
            raise CalibrationError("Invalid microsteps_per_rev")
        return self.pitch_um / self.microsteps_per_rev

    @property
    def steps_per_um(self) -> float:
        um = self.um_per_microstep
        if um <= 0:
            raise CalibrationError("um_per_microstep must be > 0")
        return 1.0 / um

    def um_to_steps(self, micrometers: float) -> int:
        return int(round(micrometers * self.steps_per_um))

    def steps_to_um(self, steps: int | float) -> float:
        return float(steps) * self.um_per_microstep


@dataclass(frozen=True)
class MagnificationCalibration:
    steps_per_click: int
    positions: dict[str, int]

    def named_position(self, name: str) -> int:
        key = name.strip()
        if key not in self.positions:
            known = ", ".join(sorted(self.positions))
            raise CalibrationError(f"Unknown magnification position {name!r}; known: {known}")
        return int(self.positions[key])


@dataclass(frozen=True)
class IlluminationCalibration:
    steps_per_level: int
    min_level: int
    max_level: int
    min_steps: int
    max_steps: int

    def level_to_steps(self, level: float) -> int:
        if level < self.min_level or level > self.max_level:
            raise CalibrationError(
                f"Illumination level {level} outside [{self.min_level}, {self.max_level}]"
            )
        span = self.max_level - self.min_level
        if span <= 0:
            raise CalibrationError("Invalid illumination level span")
        ratio = (level - self.min_level) / span
        steps = self.min_steps + ratio * (self.max_steps - self.min_steps)
        return int(round(steps))

    def steps_to_level(self, steps: int) -> float:
        span_steps = self.max_steps - self.min_steps
        if span_steps <= 0:
            return float(self.min_level)
        ratio = (steps - self.min_steps) / span_steps
        return self.min_level + ratio * (self.max_level - self.min_level)


class CalibrationService:
    """Host-side calibration derived from YAML."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.focus = self._build_focus()
        self.magnification = self._build_magnification()
        self.illumination = self._build_illumination()

    def _build_focus(self) -> FocusCalibration:
        motors = self.config.get("motors") or {}
        cal = (self.config.get("calibration") or {}).get("focus") or {}
        return FocusCalibration(
            leadscrew_pitch_mm=float(cal.get("leadscrew_pitch_mm", 1.0)),
            gear_ratio=float(cal.get("gear_ratio", 1.0)),
            full_steps_per_rev=int(motors.get("full_steps_per_rev", 200)),
            microstep=microstep_mode(self.config),
        )

    def _build_magnification(self) -> MagnificationCalibration:
        cal = (self.config.get("calibration") or {}).get("magnification") or {}
        positions = {str(k): int(v) for k, v in (cal.get("positions") or {}).items()}
        return MagnificationCalibration(
            steps_per_click=int(cal.get("steps_per_click", 3200)),
            positions=positions,
        )

    def _build_illumination(self) -> IlluminationCalibration:
        cal = (self.config.get("calibration") or {}).get("illumination") or {}
        axis = (self.config.get("axes") or {}).get("I") or {}
        return IlluminationCalibration(
            steps_per_level=int(cal.get("steps_per_level", 320)),
            min_level=int(cal.get("min_level", 0)),
            max_level=int(cal.get("max_level", 100)),
            min_steps=int(axis.get("min_steps", 0)),
            max_steps=int(axis.get("max_steps", 32000)),
        )

    def report(self) -> dict[str, Any]:
        return {
            "focus": {
                "um_per_microstep": self.focus.um_per_microstep,
                "steps_per_um": self.focus.steps_per_um,
                "microsteps_per_rev": self.focus.microsteps_per_rev,
                "leadscrew_pitch_mm": self.focus.leadscrew_pitch_mm,
                "gear_ratio": self.focus.gear_ratio,
                "microstep": self.focus.microstep,
            },
            "magnification": {
                "steps_per_click": self.magnification.steps_per_click,
                "positions": dict(self.magnification.positions),
            },
            "illumination": {
                "steps_per_level": self.illumination.steps_per_level,
                "min_level": self.illumination.min_level,
                "max_level": self.illumination.max_level,
            },
        }

    def to_steps(self, axis: Axis, value: float, unit: str) -> int:
        unit = unit.lower().strip()
        if axis == Axis.FOCUS:
            if unit in ("step", "steps"):
                return int(round(value))
            if unit in ("um", "µm", "micrometer", "micrometre"):
                return self.focus.um_to_steps(value)
            if unit in ("mm",):
                return self.focus.um_to_steps(value * 1000.0)
            raise CalibrationError(f"Unsupported focus unit: {unit}")
        if axis == Axis.MAGNIFICATION:
            if unit in ("step", "steps"):
                return int(round(value))
            if unit in ("click", "clicks", "detent"):
                return int(round(value * self.magnification.steps_per_click))
            if unit in ("name", "objective", "label"):
                raise CalibrationError("Use set_objective(name) for named magnification")
            raise CalibrationError(f"Unsupported magnification unit: {unit}")
        if axis == Axis.ILLUMINATION:
            if unit in ("step", "steps"):
                return int(round(value))
            if unit in ("level", "percent", "%"):
                return self.illumination.level_to_steps(value)
            raise CalibrationError(f"Unsupported illumination unit: {unit}")
        raise CalibrationError(f"Unknown axis {axis}")
