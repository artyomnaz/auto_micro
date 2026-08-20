"""High-level microscope axis controller."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

from microscope_control.axes import Axis
from microscope_control.calibration import CalibrationService
from microscope_control.config import load_config, microstep_mode
from microscope_control.diagnostics import Diagnostics
from microscope_control.exceptions import MicroscopeControlError, SafetyError
from microscope_control.logging_util import get_logger, setup_logging
from microscope_control.motion import MotionPlan, MotionPlanner
from microscope_control.presets import PresetLibrary
from microscope_control import protocol as proto
from microscope_control.protocol import LimitsReport, ProtocolError, StatusReport, VersionInfo
from microscope_control.safety import SafetyGuard
from microscope_control.transport.firmware_runtime import FirmwareRuntimePort
from microscope_control.transport.serial_port import SerialPort


def create_transport(config: dict[str, Any]):
    connection = str(config.get("connection", "firmware_runtime")).lower().strip()
    if connection in ("firmware_runtime", "runtime", "inprocess"):
        return FirmwareRuntimePort(config)
    if connection == "serial":
        serial_cfg = config.get("serial", {})
        return SerialPort(
            port=str(serial_cfg.get("port", "COM3")),
            baudrate=int(serial_cfg.get("baudrate", 115200)),
            timeout_s=float(serial_cfg.get("timeout_s", 2.0)),
            boot_delay_s=float(serial_cfg.get("boot_delay_s", 2.0)),
        )
    raise ValueError(
        f"Unknown connection {connection!r}; expected 'serial' or 'firmware_runtime'"
    )


class MicroscopeController:
    """Host-side control of focus / magnification / illumination axes."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        config_path: str | Path | None = None,
        transport=None,
    ) -> None:
        if config is None:
            config = load_config(config_path)
        self.config = config
        self.logger = setup_logging(config)
        self._transport = transport if transport is not None else create_transport(config)
        ctrl = config.get("controller", {})
        self.poll_interval_s = float(ctrl.get("poll_interval_s", 0.02))
        self.move_timeout_s = float(ctrl.get("move_timeout_s", 120.0))
        self._opened = False
        self._active_profile = str((config.get("motion_profiles") or {}).get("default", "coarse"))

        self.calibration = CalibrationService(config)
        self.safety = SafetyGuard(config)
        self.planner = MotionPlanner(config)
        self.presets = PresetLibrary(config)
        self.diagnostics = Diagnostics(self)

    def __enter__(self) -> MicroscopeController:
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        if self._opened:
            return
        self._transport.open()
        self._opened = True
        self.logger.info(
            "Opened connection=%s", self.config.get("connection", "firmware_runtime")
        )

        ctrl = self.config.get("controller") or {}
        if ctrl.get("apply_axis_defaults_on_open", True):
            for axis in Axis:
                acfg = self.config.get("axes", {}).get(axis.value, {})
                if "default_speed_sps" in acfg:
                    self.set_speed(axis, float(acfg["default_speed_sps"]))
                if "default_accel_sps2" in acfg:
                    self.set_acceleration(axis, float(acfg["default_accel_sps2"]))

        if ctrl.get("apply_profile_on_open", True):
            try:
                self.apply_profile(self._active_profile)
            except (ProtocolError, MicroscopeControlError) as exc:
                self.logger.warning("Could not apply default profile: %s", exc)

        if (self.config.get("diagnostics") or {}).get("self_test_on_open"):
            self.diagnostics.self_test()

    def close(self) -> None:
        if self._opened:
            try:
                self._transport.close()
            finally:
                self._opened = False
                self.logger.info("Connection closed")

    def _log_command(self, command: str) -> None:
        cfg = self.config.get("logging") or {}
        if cfg.get("log_commands", True):
            self.logger.debug("TX %s", command)

    def _log_response(self, response: str, *, is_status: bool = False) -> None:
        cfg = self.config.get("logging") or {}
        if is_status and not cfg.get("log_status_polls", False):
            return
        if cfg.get("log_commands", True):
            self.logger.debug("RX %s", response)

    def _transact(self, command: str, *, is_status: bool = False) -> str:
        if not self._opened:
            raise RuntimeError("Controller is not open; call open() first")
        self._log_command(command)
        self._transport.write_line(command)
        response = self._transport.read_line()
        self._log_response(response, is_status=is_status)
        return response

    # --- low-level protocol -------------------------------------------------

    def ping(self) -> None:
        proto.ensure_ok(self._transact(proto.encode_ping()))

    def version(self) -> VersionInfo:
        return proto.parse_version(self._transact(proto.encode_version()))

    def enable(self) -> None:
        proto.ensure_ok(self._transact(proto.encode_enable()))

    def disable(self) -> None:
        proto.ensure_ok(self._transact(proto.encode_disable()))

    def stop(self) -> None:
        proto.ensure_ok(self._transact(proto.encode_stop()))

    def status(self) -> StatusReport:
        return proto.parse_status(self._transact(proto.encode_status(), is_status=True))

    def limits(self) -> LimitsReport:
        return proto.parse_limits(self._transact(proto.encode_limits()))

    def set_speed(self, axis: Axis | str, steps_per_sec: float) -> None:
        axis = Axis.parse(axis) if isinstance(axis, str) else axis
        steps_per_sec = self.safety.clamp_speed(axis, float(steps_per_sec))
        proto.ensure_ok(self._transact(proto.encode_setspeed(axis, steps_per_sec)))

    def set_acceleration(self, axis: Axis | str, steps_per_sec2: float) -> None:
        axis = Axis.parse(axis) if isinstance(axis, str) else axis
        steps_per_sec2 = self.safety.clamp_accel(axis, float(steps_per_sec2))
        proto.ensure_ok(self._transact(proto.encode_setacc(axis, steps_per_sec2)))

    def set_position_counter(self, axis: Axis | str, position: int) -> None:
        """Redefine current step counter without moving the motor."""
        axis = Axis.parse(axis) if isinstance(axis, str) else axis
        self.safety.check_target(axis, int(position))
        proto.ensure_ok(self._transact(proto.encode_setpos(axis, int(position))))

    def zero(self, axis: Axis | str) -> None:
        self.set_position_counter(axis, 0)

    def apply_profile(self, name: str) -> None:
        profile = self.planner.get_profile(name)
        for axis in Axis:
            speed = self.safety.clamp_speed(axis, profile.scale_speed(self.planner.base_speed(axis)))
            accel = self.safety.clamp_accel(axis, profile.scale_accel(self.planner.base_accel(axis)))
            self.set_speed(axis, speed)
            self.set_acceleration(axis, accel)
        # Inform firmware of profile name when supported.
        try:
            proto.ensure_ok(self._transact(proto.encode_setprofile(name)))
        except ProtocolError:
            pass
        self._active_profile = name
        self.logger.info("Applied motion profile %s", name)

    # --- motion -------------------------------------------------------------

    def move(self, axis: Axis | str, steps: int, *, wait: bool = True) -> StatusReport:
        axis = Axis.parse(axis) if isinstance(axis, str) else axis
        st = self.status()
        self.safety.check_enabled(st.enabled)
        self.safety.check_relative(axis, st.position(axis), int(steps))
        proto.ensure_ok(self._transact(proto.encode_move(axis, int(steps))))
        if wait:
            return self.wait_until_idle()
        return self.status()

    def goto(self, axis: Axis | str, position: int, *, wait: bool = True) -> StatusReport:
        axis = Axis.parse(axis) if isinstance(axis, str) else axis
        st = self.status()
        self.safety.check_enabled(st.enabled)
        self.safety.check_target(axis, int(position))
        proto.ensure_ok(self._transact(proto.encode_goto(axis, int(position))))
        if wait:
            return self.wait_until_idle()
        return self.status()

    def home(self, axis: Axis | str, *, wait: bool = True) -> StatusReport:
        axis = Axis.parse(axis) if isinstance(axis, str) else axis
        home_pos = int((self.config.get("axes") or {}).get(axis.value, {}).get("home_position", 0))
        st = self.status()
        self.safety.check_enabled(st.enabled)
        self.safety.check_target(axis, home_pos)
        if home_pos == 0:
            proto.ensure_ok(self._transact(proto.encode_home(axis)))
        else:
            proto.ensure_ok(self._transact(proto.encode_goto(axis, home_pos)))
        if wait:
            return self.wait_until_idle()
        return self.status()

    def home_all(self, *, wait: bool = True) -> StatusReport:
        self.enable()
        for axis in (Axis.ILLUMINATION, Axis.MAGNIFICATION, Axis.FOCUS):
            self.home(axis, wait=wait)
        return self.status()

    def sync_goto(
        self,
        positions: Mapping[Axis | str, int],
        *,
        wait: bool = True,
        profile: str | None = None,
    ) -> StatusReport:
        plan = self.planner.plan_absolute(positions, profile=profile, sync=True)
        return self.execute_plan(plan, wait=wait)

    def execute_plan(self, plan: MotionPlan, *, wait: bool = True) -> StatusReport:
        if plan.profile:
            self.apply_profile(plan.profile)
        st = self.status()
        self.safety.check_enabled(st.enabled)
        for target in plan.targets:
            self.safety.check_target(target.axis, target.position)

        if plan.sync and len(plan.targets) > 1:
            mapping = plan.by_axis()
            proto.ensure_ok(self._transact(proto.encode_sync(mapping)))
            if wait:
                return self.wait_until_idle()
            return self.status()

        for target in self.planner.iter_sequential(plan):
            proto.ensure_ok(self._transact(proto.encode_goto(target.axis, target.position)))
            if wait:
                self.wait_until_idle()
        return self.status()

    def apply_preset(self, name: str, *, wait: bool = True) -> StatusReport:
        preset = self.presets.get(name)
        self.enable()
        if preset.profile:
            self.apply_profile(preset.profile)
        return self.sync_goto(preset.positions, wait=wait)

    # --- calibrated helpers -------------------------------------------------

    def move_focus_um(self, micrometers: float, *, wait: bool = True) -> StatusReport:
        return self.move(Axis.FOCUS, self.calibration.focus.um_to_steps(micrometers), wait=wait)

    def set_focus_um(self, micrometers: float, *, wait: bool = True) -> StatusReport:
        return self.goto(Axis.FOCUS, self.calibration.focus.um_to_steps(micrometers), wait=wait)

    def jog(self, axis: Axis | str, *, fine: bool = False, wait: bool = True) -> StatusReport:
        axis = Axis.parse(axis) if isinstance(axis, str) else axis
        acfg = (self.config.get("axes") or {}).get(axis.value) or {}
        steps = int(acfg.get("fine_jog_steps" if fine else "jog_steps", 1))
        return self.move(axis, steps, wait=wait)

    def set_objective(self, name: str, *, wait: bool = True) -> StatusReport:
        pos = self.calibration.magnification.named_position(name)
        self.enable()
        return self.goto(Axis.MAGNIFICATION, pos, wait=wait)

    def set_illumination_level(self, level: float, *, wait: bool = True) -> StatusReport:
        pos = self.calibration.illumination.level_to_steps(level)
        self.enable()
        return self.goto(Axis.ILLUMINATION, pos, wait=wait)

    def move_focus(self, steps: int, *, wait: bool = True) -> StatusReport:
        return self.move(Axis.FOCUS, steps, wait=wait)

    def move_magnification(self, steps: int, *, wait: bool = True) -> StatusReport:
        return self.move(Axis.MAGNIFICATION, steps, wait=wait)

    def move_illumination(self, steps: int, *, wait: bool = True) -> StatusReport:
        return self.move(Axis.ILLUMINATION, steps, wait=wait)

    def set_focus(self, position: int, *, wait: bool = True) -> StatusReport:
        return self.goto(Axis.FOCUS, position, wait=wait)

    def set_magnification(self, position: int, *, wait: bool = True) -> StatusReport:
        return self.goto(Axis.MAGNIFICATION, position, wait=wait)

    def set_illumination(self, position: int, *, wait: bool = True) -> StatusReport:
        return self.goto(Axis.ILLUMINATION, position, wait=wait)

    def wait_until_idle(self, timeout_s: float | None = None) -> StatusReport:
        timeout = self.move_timeout_s if timeout_s is None else timeout_s
        deadline = time.monotonic() + timeout
        report = self.status()
        while report.moving:
            if time.monotonic() > deadline:
                raise TimeoutError(f"Motion did not finish within {timeout}s")
            time.sleep(self.poll_interval_s)
            report = self.status()
        settle = self.safety.settle_delay_s()
        if settle > 0:
            time.sleep(settle)
            report = self.status()
        return report

    def steps_per_um(self, axis: Axis = Axis.FOCUS) -> float:
        if axis != Axis.FOCUS:
            raise SafetyError("steps_per_um is defined for focus axis only")
        return self.calibration.focus.steps_per_um

    def focus_um_to_steps(self, micrometers: float) -> int:
        return self.calibration.focus.um_to_steps(micrometers)

    def focus_steps_to_um(self, steps: int) -> float:
        return self.calibration.focus.steps_to_um(steps)

    def snapshot(self) -> dict[str, Any]:
        st = self.status()
        return {
            "connection": self.config.get("connection"),
            "profile": self._active_profile,
            "microstep_config": microstep_mode(self.config),
            "status": st.as_dict(),
            "calibration": self.calibration.report(),
            "presets": self.presets.list(),
            "profiles": self.planner.list_profiles(),
        }
