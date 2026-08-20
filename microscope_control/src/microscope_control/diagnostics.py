"""Host-side diagnostics: ping, self-test, open-loop repeatability sweep."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from microscope_control.axes import Axis
from microscope_control.exceptions import DiagnosticsError

if TYPE_CHECKING:
    from microscope_control.controller import MicroscopeController


@dataclass(frozen=True)
class RepeatabilityResult:
    axis: Axis
    amplitude_steps: int
    cycles: int
    final_position: int
    expected_position: int
    residual_steps: int
    samples: tuple[int, ...]

    @property
    def ok(self) -> bool:
        return self.residual_steps == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis.value,
            "amplitude_steps": self.amplitude_steps,
            "cycles": self.cycles,
            "final_position": self.final_position,
            "expected_position": self.expected_position,
            "residual_steps": self.residual_steps,
            "ok": self.ok,
            "samples": list(self.samples),
            "note": (
                "Open-loop residual measures command accounting fidelity on the "
                "controller twin/firmware counters — not optical <0.5 µm metrology."
            ),
        }


class Diagnostics:
    def __init__(self, controller: MicroscopeController) -> None:
        self.controller = controller
        self.cfg = (controller.config.get("diagnostics") or {})

    def ping_with_retries(self) -> None:
        retries = int(self.cfg.get("ping_retries", 3))
        delay = float(self.cfg.get("ping_retry_delay_s", 0.2))
        last_exc: Exception | None = None
        for attempt in range(max(1, retries)):
            try:
                self.controller.ping()
                return
            except Exception as exc:  # noqa: BLE001 — collect and re-raise
                last_exc = exc
                if attempt + 1 < retries:
                    time.sleep(delay)
        raise DiagnosticsError(f"PING failed after {retries} attempts: {last_exc}")

    def self_test(self) -> dict[str, Any]:
        self.ping_with_retries()
        version = self.controller.version()
        limits = self.controller.limits()
        status = self.controller.status()
        report = {
            "ping": "ok",
            "version": {
                "firmware_id": version.firmware_id,
                "protocol_version": version.protocol_version,
                "axes": version.axes,
                "extra": version.extra,
            },
            "limits": {a.value: list(limits.limits[a]) for a in Axis},
            "status": status.as_dict(),
            "calibration": self.controller.calibration.report(),
        }
        return report

    def repeatability_sweep(self) -> RepeatabilityResult:
        """Round-trip relative moves; residual should be 0 in open-loop step space."""
        rep = self.cfg.get("repeatability") or {}
        axis = Axis.parse(str(rep.get("axis", "F")))
        amplitude = int(rep.get("amplitude_steps", 320))
        cycles = int(rep.get("cycles", 5))
        settle = float(rep.get("settle_s", 0.1))

        self.controller.enable()
        start = self.controller.status().position(axis)
        samples: list[int] = [start]
        for _ in range(cycles):
            self.controller.move(axis, amplitude, wait=True)
            time.sleep(settle)
            samples.append(self.controller.status().position(axis))
            self.controller.move(axis, -amplitude, wait=True)
            time.sleep(settle)
            samples.append(self.controller.status().position(axis))

        final = self.controller.status().position(axis)
        residual = final - start
        return RepeatabilityResult(
            axis=axis,
            amplitude_steps=amplitude,
            cycles=cycles,
            final_position=final,
            expected_position=start,
            residual_steps=residual,
            samples=tuple(samples),
        )
