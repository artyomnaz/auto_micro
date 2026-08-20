"""Serial text protocol shared by Arduino firmware and host/firmware_runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from microscope_control.axes import Axis

OK = "ok"
ERROR_PREFIX = "error:"
STATUS_PREFIX = "status:"
VERSION_PREFIX = "version:"
LIMITS_PREFIX = "limits:"
DIAG_PREFIX = "diag:"

PROTOCOL_VERSION = "1.1"
FIRMWARE_ID = "microscope_controller"


class ProtocolError(RuntimeError):
    """Raised when the device returns an error or an unexpected reply."""


@dataclass(frozen=True)
class StatusReport:
    enabled: bool
    moving: bool
    positions: Mapping[Axis, int]
    targets: Mapping[Axis, int]
    speeds: Mapping[Axis, float]
    accels: Mapping[Axis, float]
    microstep: int = 16
    profile: str = ""

    def position(self, axis: Axis) -> int:
        return int(self.positions[axis])

    def target(self, axis: Axis) -> int:
        return int(self.targets.get(axis, self.positions[axis]))

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "moving": self.moving,
            "microstep": self.microstep,
            "profile": self.profile,
            "positions": {a.value: self.positions[a] for a in Axis},
            "targets": {a.value: self.target(a) for a in Axis},
            "speeds": {a.value: self.speeds[a] for a in Axis},
            "accels": {a.value: self.accels[a] for a in Axis},
        }


@dataclass(frozen=True)
class VersionInfo:
    firmware_id: str
    protocol_version: str
    axes: str = "F/M/I"
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LimitsReport:
    limits: Mapping[Axis, tuple[int, int]]


def encode_ping() -> str:
    return "PING"


def encode_version() -> str:
    return "VERSION"


def encode_enable() -> str:
    return "ENABLE"


def encode_disable() -> str:
    return "DISABLE"


def encode_stop() -> str:
    return "STOP"


def encode_status() -> str:
    return "STATUS"


def encode_limits() -> str:
    return "LIMITS"


def encode_diag() -> str:
    return "DIAG"


def encode_move(axis: Axis, steps: int) -> str:
    return f"MOVE {axis.value} {int(steps)}"


def encode_goto(axis: Axis, position: int) -> str:
    return f"GOTO {axis.value} {int(position)}"


def encode_home(axis: Axis) -> str:
    return f"HOME {axis.value}"


def encode_setpos(axis: Axis, position: int) -> str:
    """Set current position counter without moving (software zeroing)."""
    return f"SETPOS {axis.value} {int(position)}"


def encode_setspeed(axis: Axis, steps_per_sec: float) -> str:
    return f"SETSPEED {axis.value} {float(steps_per_sec):.3f}"


def encode_setacc(axis: Axis, steps_per_sec2: float) -> str:
    return f"SETACC {axis.value} {float(steps_per_sec2):.3f}"


def encode_setprofile(name: str) -> str:
    return f"SETPROFILE {name}"


def encode_sync(positions: Mapping[Axis, int]) -> str:
    """Multi-axis absolute move acknowledged as one command."""
    parts = ["SYNC"]
    for axis in (Axis.FOCUS, Axis.MAGNIFICATION, Axis.ILLUMINATION):
        if axis in positions:
            parts.append(f"{axis.value}={int(positions[axis])}")
    if len(parts) == 1:
        raise ProtocolError("SYNC requires at least one axis")
    return " ".join(parts)


def parse_response(line: str) -> str:
    text = line.strip()
    if not text:
        raise ProtocolError("Empty response from device")
    if text.startswith(ERROR_PREFIX):
        raise ProtocolError(text[len(ERROR_PREFIX) :])
    return text


def ensure_ok(line: str) -> None:
    text = parse_response(line)
    if text != OK:
        raise ProtocolError(f"Expected '{OK}', got {text!r}")


def _parse_fields(payload: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in payload.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ProtocolError(f"Malformed field: {part!r}")
        key, value = part.split("=", 1)
        fields[key.strip().lower()] = value.strip()
    return fields


def parse_status(line: str) -> StatusReport:
    text = parse_response(line)
    if not text.startswith(STATUS_PREFIX):
        raise ProtocolError(f"Expected status line, got {text!r}")
    fields = _parse_fields(text[len(STATUS_PREFIX) :])

    def require(key: str) -> str:
        if key not in fields:
            raise ProtocolError(f"Missing status field: {key}")
        return fields[key]

    positions = {
        Axis.FOCUS: int(require("f")),
        Axis.MAGNIFICATION: int(require("m")),
        Axis.ILLUMINATION: int(require("i")),
    }
    targets = {
        Axis.FOCUS: int(fields.get("tf", fields["f"])),
        Axis.MAGNIFICATION: int(fields.get("tm", fields["m"])),
        Axis.ILLUMINATION: int(fields.get("ti", fields["i"])),
    }
    speeds = {
        Axis.FOCUS: float(require("sf")),
        Axis.MAGNIFICATION: float(require("sm")),
        Axis.ILLUMINATION: float(require("si")),
    }
    accels = {
        Axis.FOCUS: float(require("af")),
        Axis.MAGNIFICATION: float(require("am")),
        Axis.ILLUMINATION: float(require("ai")),
    }
    return StatusReport(
        enabled=require("en") in ("1", "true", "yes"),
        moving=require("mv") in ("1", "true", "yes"),
        positions=positions,
        targets=targets,
        speeds=speeds,
        accels=accels,
        microstep=int(fields.get("ms", "16")),
        profile=fields.get("pf", ""),
    )


def format_status(
    *,
    enabled: bool,
    moving: bool,
    positions: Mapping[Axis, int],
    speeds: Mapping[Axis, float],
    accels: Mapping[Axis, float],
    targets: Mapping[Axis, int] | None = None,
    microstep: int = 16,
    profile: str = "",
) -> str:
    targets = targets or positions
    return (
        f"{STATUS_PREFIX}"
        f"en={1 if enabled else 0},"
        f"mv={1 if moving else 0},"
        f"f={int(positions[Axis.FOCUS])},"
        f"m={int(positions[Axis.MAGNIFICATION])},"
        f"i={int(positions[Axis.ILLUMINATION])},"
        f"tf={int(targets[Axis.FOCUS])},"
        f"tm={int(targets[Axis.MAGNIFICATION])},"
        f"ti={int(targets[Axis.ILLUMINATION])},"
        f"sf={float(speeds[Axis.FOCUS]):.3f},"
        f"sm={float(speeds[Axis.MAGNIFICATION]):.3f},"
        f"si={float(speeds[Axis.ILLUMINATION]):.3f},"
        f"af={float(accels[Axis.FOCUS]):.3f},"
        f"am={float(accels[Axis.MAGNIFICATION]):.3f},"
        f"ai={float(accels[Axis.ILLUMINATION]):.3f},"
        f"ms={int(microstep)},"
        f"pf={profile}"
    )


def parse_version(line: str) -> VersionInfo:
    text = parse_response(line)
    if not text.startswith(VERSION_PREFIX):
        raise ProtocolError(f"Expected version line, got {text!r}")
    fields = _parse_fields(text[len(VERSION_PREFIX) :])
    return VersionInfo(
        firmware_id=fields.get("id", FIRMWARE_ID),
        protocol_version=fields.get("proto", PROTOCOL_VERSION),
        axes=fields.get("axes", "F/M/I"),
        extra={k: v for k, v in fields.items() if k not in ("id", "proto", "axes")},
    )


def format_version(
    *,
    firmware_id: str = FIRMWARE_ID,
    protocol_version: str = PROTOCOL_VERSION,
    axes: str = "F/M/I",
    **extra: str,
) -> str:
    parts = [
        f"{VERSION_PREFIX}id={firmware_id}",
        f"proto={protocol_version}",
        f"axes={axes}",
    ]
    for key, value in extra.items():
        parts.append(f"{key}={value}")
    return ",".join(parts)


def parse_limits(line: str) -> LimitsReport:
    text = parse_response(line)
    if not text.startswith(LIMITS_PREFIX):
        raise ProtocolError(f"Expected limits line, got {text!r}")
    fields = _parse_fields(text[len(LIMITS_PREFIX) :])
    limits: dict[Axis, tuple[int, int]] = {}
    for axis, lo_key, hi_key in (
        (Axis.FOCUS, "fmin", "fmax"),
        (Axis.MAGNIFICATION, "mmin", "mmax"),
        (Axis.ILLUMINATION, "imin", "imax"),
    ):
        if lo_key not in fields or hi_key not in fields:
            raise ProtocolError(f"Missing limits for {axis.value}")
        limits[axis] = (int(fields[lo_key]), int(fields[hi_key]))
    return LimitsReport(limits=limits)


def format_limits(limits: Mapping[Axis, tuple[int, int]]) -> str:
    return (
        f"{LIMITS_PREFIX}"
        f"fmin={limits[Axis.FOCUS][0]},fmax={limits[Axis.FOCUS][1]},"
        f"mmin={limits[Axis.MAGNIFICATION][0]},mmax={limits[Axis.MAGNIFICATION][1]},"
        f"imin={limits[Axis.ILLUMINATION][0]},imax={limits[Axis.ILLUMINATION][1]}"
    )


def parse_sync_args(parts: list[str]) -> dict[Axis, int]:
    """Parse SYNC F=1 M=2 I=3 style arguments (parts[0] is SYNC)."""
    result: dict[Axis, int] = {}
    for token in parts[1:]:
        if "=" not in token:
            raise ValueError(f"bad sync token {token}")
        key, value = token.split("=", 1)
        result[Axis.parse(key)] = int(value)
    if not result:
        raise ValueError("empty sync")
    return result
