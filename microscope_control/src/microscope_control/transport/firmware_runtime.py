"""In-process firmware twin that speaks the same serial command protocol."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from microscope_control.axes import Axis
from microscope_control.config import microstep_mode
from microscope_control.protocol import (
    OK,
    ERROR_PREFIX,
    PROTOCOL_VERSION,
    FIRMWARE_ID,
    format_limits,
    format_status,
    format_version,
    parse_sync_args,
)


@dataclass
class AxisState:
    position: int = 0
    target: int = 0
    speed_sps: float = 800.0
    accel_sps2: float = 1600.0
    min_steps: int = -200000
    max_steps: int = 200000
    invert: bool = False

    def is_moving(self) -> bool:
        return self.position != self.target


@dataclass
class FirmwareState:
    enabled: bool = False
    profile: str = "coarse"
    microstep: int = 16
    axes: dict[Axis, AxisState] = field(default_factory=dict)

    def any_moving(self) -> bool:
        return any(ax.is_moving() for ax in self.axes.values())


def _axis_cfg(config: dict[str, Any], axis: Axis) -> dict[str, Any]:
    return dict(config.get("axes", {}).get(axis.value, {}))


class FirmwareRuntime:
    """Command processor mirroring microscope_controller.ino behaviour."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.config = cfg
        self.state = FirmwareState(
            axes={},
            microstep=microstep_mode(cfg) if cfg else 16,
            profile=str((cfg.get("motion_profiles") or {}).get("default", "coarse")),
        )
        defaults = {
            Axis.FOCUS: (800.0, 1600.0, -200000, 200000),
            Axis.MAGNIFICATION: (600.0, 1200.0, -100000, 100000),
            Axis.ILLUMINATION: (400.0, 800.0, 0, 32000),
        }
        for axis, (spd, acc, lo, hi) in defaults.items():
            acfg = _axis_cfg(cfg, axis)
            self.state.axes[axis] = AxisState(
                speed_sps=float(acfg.get("default_speed_sps", spd)),
                accel_sps2=float(acfg.get("default_accel_sps2", acc)),
                min_steps=int(acfg.get("min_steps", lo)),
                max_steps=int(acfg.get("max_steps", hi)),
                invert=bool(acfg.get("invert_direction", False)),
                position=int(acfg.get("home_position", 0)),
                target=int(acfg.get("home_position", 0)),
            )

    def tick(self, dt: float) -> None:
        if not self.state.enabled or dt <= 0:
            return
        for ax in self.state.axes.values():
            if ax.position == ax.target:
                continue
            max_steps = max(1, int(ax.speed_sps * dt))
            delta = ax.target - ax.position
            step = max(-max_steps, min(max_steps, delta))
            ax.position += step

    def handle_line(self, line: str) -> str:
        cmd = line.strip()
        if not cmd:
            return f"{ERROR_PREFIX}empty"
        parts = cmd.split()
        op = parts[0].upper()

        try:
            if op == "PING":
                return OK
            if op == "VERSION":
                return format_version(
                    firmware_id=FIRMWARE_ID,
                    protocol_version=PROTOCOL_VERSION,
                    runtime="firmware_runtime",
                    ms=str(self.state.microstep),
                )
            if op == "ENABLE":
                self.state.enabled = True
                return OK
            if op == "DISABLE":
                self.state.enabled = False
                for ax in self.state.axes.values():
                    ax.target = ax.position
                return OK
            if op == "STOP":
                for ax in self.state.axes.values():
                    ax.target = ax.position
                return OK
            if op == "STATUS":
                return self._status_line()
            if op == "LIMITS":
                return format_limits(
                    {a: (ax.min_steps, ax.max_steps) for a, ax in self.state.axes.items()}
                )
            if op == "DIAG":
                moving = 1 if self.state.any_moving() else 0
                return (
                    f"diag:en={1 if self.state.enabled else 0},mv={moving},"
                    f"ms={self.state.microstep},pf={self.state.profile}"
                )
            if op == "SETPROFILE":
                if len(parts) < 2:
                    return f"{ERROR_PREFIX}missing profile"
                self.state.profile = parts[1]
                return OK
            if op == "SYNC":
                return self._sync(parts)
            if op == "MOVE":
                axis, steps = self._parse_axis_int(parts, 2)
                if self.state.axes[axis].invert:
                    steps = -steps
                return self._move_relative(axis, steps)
            if op == "GOTO":
                axis, pos = self._parse_axis_int(parts, 2)
                return self._goto(axis, pos)
            if op == "HOME":
                axis = self._parse_axis(parts, 1)
                home = int(_axis_cfg(self.config, axis).get("home_position", 0))
                return self._goto(axis, home)
            if op == "SETPOS":
                axis, pos = self._parse_axis_int(parts, 2)
                ax = self.state.axes[axis]
                if pos < ax.min_steps or pos > ax.max_steps:
                    return f"{ERROR_PREFIX}soft limit"
                ax.position = pos
                ax.target = pos
                return OK
            if op == "SETSPEED":
                axis, value = self._parse_axis_float(parts, 2)
                if value <= 0:
                    return f"{ERROR_PREFIX}speed must be > 0"
                self.state.axes[axis].speed_sps = value
                return OK
            if op == "SETACC":
                axis, value = self._parse_axis_float(parts, 2)
                if value <= 0:
                    return f"{ERROR_PREFIX}accel must be > 0"
                self.state.axes[axis].accel_sps2 = value
                return OK
            return f"{ERROR_PREFIX}unknown command"
        except ValueError as exc:
            return f"{ERROR_PREFIX}{exc}"

    def _status_line(self) -> str:
        positions = {a: ax.position for a, ax in self.state.axes.items()}
        targets = {a: ax.target for a, ax in self.state.axes.items()}
        speeds = {a: ax.speed_sps for a, ax in self.state.axes.items()}
        accels = {a: ax.accel_sps2 for a, ax in self.state.axes.items()}
        return format_status(
            enabled=self.state.enabled,
            moving=self.state.any_moving(),
            positions=positions,
            targets=targets,
            speeds=speeds,
            accels=accels,
            microstep=self.state.microstep,
            profile=self.state.profile,
        )

    def _parse_axis(self, parts: list[str], index: int) -> Axis:
        if len(parts) <= index:
            raise ValueError("missing axis")
        return Axis.parse(parts[index])

    def _parse_axis_int(self, parts: list[str], value_index: int) -> tuple[Axis, int]:
        axis = self._parse_axis(parts, 1)
        if len(parts) <= value_index:
            raise ValueError("missing value")
        return axis, int(parts[value_index])

    def _parse_axis_float(self, parts: list[str], value_index: int) -> tuple[Axis, float]:
        axis = self._parse_axis(parts, 1)
        if len(parts) <= value_index:
            raise ValueError("missing value")
        return axis, float(parts[value_index])

    def _goto(self, axis: Axis, position: int) -> str:
        if not self.state.enabled:
            return f"{ERROR_PREFIX}disabled"
        ax = self.state.axes[axis]
        if position < ax.min_steps or position > ax.max_steps:
            return f"{ERROR_PREFIX}soft limit"
        ax.target = int(position)
        return OK

    def _move_relative(self, axis: Axis, steps: int) -> str:
        ax = self.state.axes[axis]
        return self._goto(axis, ax.position + int(steps))

    def _sync(self, parts: list[str]) -> str:
        if not self.state.enabled:
            return f"{ERROR_PREFIX}disabled"
        try:
            mapping = parse_sync_args(parts)
        except ValueError as exc:
            return f"{ERROR_PREFIX}{exc}"
        for axis, pos in mapping.items():
            ax = self.state.axes[axis]
            if pos < ax.min_steps or pos > ax.max_steps:
                return f"{ERROR_PREFIX}soft limit"
        for axis, pos in mapping.items():
            self.state.axes[axis].target = int(pos)
        return OK


class FirmwareRuntimePort:
    """Transport that routes lines through FirmwareRuntime (no COM port)."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.runtime = FirmwareRuntime(config)
        self._inbox: deque[str] = deque()
        self._open = False
        self._last_tick = time.monotonic()

    def open(self) -> None:
        self._open = True
        self._inbox.clear()
        self._last_tick = time.monotonic()

    def close(self) -> None:
        self._open = False
        self._inbox.clear()

    def is_open(self) -> bool:
        return self._open

    def write_line(self, line: str) -> None:
        if not self._open:
            raise RuntimeError("FirmwareRuntimePort is not open")
        self._advance_clock()
        reply = self.runtime.handle_line(line)
        self._inbox.append(reply)

    def read_line(self) -> str:
        if not self._open:
            raise RuntimeError("FirmwareRuntimePort is not open")
        if not self._inbox:
            raise TimeoutError("No response from firmware_runtime")
        return self._inbox.popleft()

    def reset_input(self) -> None:
        self._inbox.clear()

    def _advance_clock(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        rt_cfg = self.runtime.config.get("firmware_runtime") or {}
        scale = float(rt_cfg.get("time_scale", 80.0))
        max_tick = float(rt_cfg.get("max_tick_s", 2.0))
        self.runtime.tick(min(max(dt, 0.0) * scale, max_tick))
