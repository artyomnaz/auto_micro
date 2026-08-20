"""Motion profiles and multi-axis sequencing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Mapping

from microscope_control.axes import Axis
from microscope_control.exceptions import ConfigError


@dataclass(frozen=True)
class MotionProfile:
    name: str
    description: str
    speed_scale: float
    accel_scale: float

    def scale_speed(self, base_sps: float) -> float:
        return max(1.0, base_sps * self.speed_scale)

    def scale_accel(self, base_accel: float) -> float:
        return max(1.0, base_accel * self.accel_scale)


@dataclass(frozen=True)
class AxisTarget:
    axis: Axis
    position: int


@dataclass(frozen=True)
class MotionPlan:
    """Ordered list of absolute targets; may be executed as sync or sequential."""

    targets: tuple[AxisTarget, ...]
    profile: str | None = None
    sync: bool = True

    def by_axis(self) -> dict[Axis, int]:
        return {t.axis: t.position for t in self.targets}


class MotionPlanner:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.profiles = self._load_profiles()
        self.default_profile_name = str(
            (config.get("motion_profiles") or {}).get("default", "coarse")
        )

    def _load_profiles(self) -> dict[str, MotionProfile]:
        raw = dict(self.config.get("motion_profiles") or {})
        raw.pop("default", None)
        profiles: dict[str, MotionProfile] = {}
        for name, body in raw.items():
            if not isinstance(body, dict):
                continue
            profiles[name] = MotionProfile(
                name=name,
                description=str(body.get("description", "")),
                speed_scale=float(body.get("speed_scale", 1.0)),
                accel_scale=float(body.get("accel_scale", 1.0)),
            )
        if not profiles:
            profiles["coarse"] = MotionProfile("coarse", "default", 1.0, 1.0)
        return profiles

    def get_profile(self, name: str | None = None) -> MotionProfile:
        key = name or self.default_profile_name
        if key not in self.profiles:
            raise ConfigError(f"Unknown motion profile: {key!r}")
        return self.profiles[key]

    def list_profiles(self) -> list[str]:
        return sorted(self.profiles)

    def base_speed(self, axis: Axis) -> float:
        return float((self.config.get("axes") or {}).get(axis.value, {}).get("default_speed_sps", 800))

    def base_accel(self, axis: Axis) -> float:
        return float(
            (self.config.get("axes") or {}).get(axis.value, {}).get("default_accel_sps2", 1600)
        )

    def plan_absolute(
        self,
        positions: Mapping[Axis | str, int],
        *,
        profile: str | None = None,
        sync: bool | None = None,
    ) -> MotionPlan:
        targets: list[AxisTarget] = []
        for key, pos in positions.items():
            axis = Axis.parse(key) if isinstance(key, str) else key
            targets.append(AxisTarget(axis=axis, position=int(pos)))
        if not targets:
            raise ConfigError("Motion plan requires at least one axis target")
        use_sync = (
            bool((self.config.get("controller") or {}).get("sync_multi_axis", True))
            if sync is None
            else sync
        )
        return MotionPlan(targets=tuple(targets), profile=profile, sync=use_sync)

    def iter_sequential(self, plan: MotionPlan) -> Iterator[AxisTarget]:
        order = (Axis.ILLUMINATION, Axis.MAGNIFICATION, Axis.FOCUS)
        ranking = {axis: i for i, axis in enumerate(order)}
        for target in sorted(plan.targets, key=lambda t: ranking.get(t.axis, 99)):
            yield target
