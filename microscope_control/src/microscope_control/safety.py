"""Safety policy checks before commanding motion."""

from __future__ import annotations

from typing import Any

from microscope_control.axes import Axis
from microscope_control.exceptions import SafetyError


class SafetyGuard:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.cfg = config.get("safety") or {}
        self.axes = config.get("axes") or {}

    @property
    def require_enable(self) -> bool:
        return bool(self.cfg.get("require_enable_before_move", True))

    @property
    def enforce_soft_limits(self) -> bool:
        return bool(self.cfg.get("enforce_soft_limits", True))

    @property
    def enforce_speed_caps(self) -> bool:
        return bool(self.cfg.get("enforce_speed_caps", True))

    def settle_delay_s(self) -> float:
        return float(self.cfg.get("settle_delay_s", 0.0))

    def check_enabled(self, enabled: bool) -> None:
        if self.require_enable and not enabled:
            raise SafetyError("Drivers are disabled; call enable() before motion")

    def check_target(self, axis: Axis, position: int) -> None:
        if not self.enforce_soft_limits:
            return
        acfg = self.axes.get(axis.value) or {}
        lo = int(acfg.get("min_steps", -2_000_000))
        hi = int(acfg.get("max_steps", 2_000_000))
        if position < lo or position > hi:
            raise SafetyError(
                f"Target {position} for axis {axis.value} outside soft limits [{lo}, {hi}]"
            )

    def check_relative(self, axis: Axis, current: int, steps: int) -> None:
        self.check_target(axis, current + steps)
        caps = self.cfg.get("max_relative_move_steps") or {}
        if axis.value in caps:
            limit = int(caps[axis.value])
            if abs(steps) > limit:
                raise SafetyError(
                    f"Relative move {steps} on {axis.value} exceeds max_relative_move_steps={limit}"
                )

    def clamp_speed(self, axis: Axis, speed_sps: float) -> float:
        if not self.enforce_speed_caps:
            return speed_sps
        acfg = self.axes.get(axis.value) or {}
        cap = float(acfg.get("max_speed_sps", speed_sps))
        if speed_sps <= 0:
            raise SafetyError("Speed must be > 0")
        if speed_sps > cap:
            return cap
        return speed_sps

    def clamp_accel(self, axis: Axis, accel_sps2: float) -> float:
        if not self.enforce_speed_caps:
            return accel_sps2
        acfg = self.axes.get(axis.value) or {}
        cap = float(acfg.get("max_accel_sps2", accel_sps2))
        if accel_sps2 <= 0:
            raise SafetyError("Acceleration must be > 0")
        if accel_sps2 > cap:
            return cap
        return accel_sps2
