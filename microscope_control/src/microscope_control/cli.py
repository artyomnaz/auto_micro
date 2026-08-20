"""Command-line interface for microscope axis control."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from microscope_control.axes import Axis
from microscope_control.config import DEFAULT_CONFIG_PATH, load_config
from microscope_control.controller import MicroscopeController
from microscope_control.exceptions import (
    CalibrationError,
    ConfigError,
    DiagnosticsError,
    MicroscopeControlError,
    SafetyError,
)
from microscope_control.protocol import ProtocolError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="microscope-control",
        description="Control focus / magnification / illumination on the automated microscope.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to YAML config (default: {DEFAULT_CONFIG_PATH})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="Check device responsiveness")
    sub.add_parser("version", help="Show firmware / protocol version")
    sub.add_parser("enable", help="Enable motor drivers")
    sub.add_parser("disable", help="Disable motor drivers")
    sub.add_parser("stop", help="Stop all motion immediately")
    sub.add_parser("status", help="Print axis status as JSON")
    sub.add_parser("limits", help="Print soft limits")
    sub.add_parser("snapshot", help="Full controller snapshot JSON")
    sub.add_parser("calibration", help="Print calibration report")
    sub.add_parser("self-test", help="Run diagnostics self-test")
    sub.add_parser("repeatability", help="Open-loop round-trip repeatability sweep")
    sub.add_parser("list-presets", help="List named acquisition presets")
    sub.add_parser("list-profiles", help="List motion profiles")

    p_move = sub.add_parser("move", help="Relative move in steps")
    p_move.add_argument("axis", type=str, help="F|M|I")
    p_move.add_argument("steps", type=int)
    p_move.add_argument("--no-wait", action="store_true")

    p_goto = sub.add_parser("goto", help="Absolute move in steps")
    p_goto.add_argument("axis", type=str, help="F|M|I")
    p_goto.add_argument("position", type=int)
    p_goto.add_argument("--no-wait", action="store_true")

    p_home = sub.add_parser("home", help="Home axis to configured home_position")
    p_home.add_argument("axis", type=str, nargs="?", default=None, help="F|M|I or omit for all")
    p_home.add_argument("--no-wait", action="store_true")

    p_spd = sub.add_parser("setspeed", help="Set axis speed (steps/s)")
    p_spd.add_argument("axis", type=str)
    p_spd.add_argument("value", type=float)

    p_acc = sub.add_parser("setacc", help="Set axis acceleration (steps/s^2)")
    p_acc.add_argument("axis", type=str)
    p_acc.add_argument("value", type=float)

    p_prof = sub.add_parser("profile", help="Apply named motion profile")
    p_prof.add_argument("name", type=str)

    p_preset = sub.add_parser("preset", help="Apply named acquisition preset")
    p_preset.add_argument("name", type=str)
    p_preset.add_argument("--no-wait", action="store_true")

    p_sync = sub.add_parser("sync", help="Synchronized multi-axis GOTO, e.g. F=0 M=3200 I=10000")
    p_sync.add_argument("targets", nargs="+", help="Axis=position tokens")
    p_sync.add_argument("--no-wait", action="store_true")
    p_sync.add_argument("--profile", type=str, default=None)

    p_focus = sub.add_parser("focus-um", help="Absolute focus position in micrometres")
    p_focus.add_argument("micrometers", type=float)
    p_focus.add_argument("--no-wait", action="store_true")

    p_obj = sub.add_parser("objective", help="Goto named magnification stop (e.g. 10x)")
    p_obj.add_argument("name", type=str)
    p_obj.add_argument("--no-wait", action="store_true")

    p_illum = sub.add_parser("illum-level", help="Set illumination level 0..100")
    p_illum.add_argument("level", type=float)
    p_illum.add_argument("--no-wait", action="store_true")

    p_zero = sub.add_parser("zero", help="Set position counter to 0 without moving")
    p_zero.add_argument("axis", type=str)

    p_jog = sub.add_parser("jog", help="Jog by configured jog_steps")
    p_jog.add_argument("axis", type=str)
    p_jog.add_argument("--fine", action="store_true")
    p_jog.add_argument("--no-wait", action="store_true")

    return parser


def _parse_sync_targets(tokens: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for token in tokens:
        if "=" not in token:
            raise ValueError(f"Invalid sync token {token!r}; expected Axis=position")
        key, value = token.split("=", 1)
        result[key] = int(value)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)

    try:
        with MicroscopeController(config=config) as ctrl:
            cmd = args.command
            if cmd == "ping":
                ctrl.ping()
                print("ok")
            elif cmd == "version":
                ver = ctrl.version()
                print(
                    json.dumps(
                        {
                            "firmware_id": ver.firmware_id,
                            "protocol_version": ver.protocol_version,
                            "axes": ver.axes,
                            "extra": ver.extra,
                        },
                        indent=2,
                    )
                )
            elif cmd == "enable":
                ctrl.enable()
                print("ok")
            elif cmd == "disable":
                ctrl.disable()
                print("ok")
            elif cmd == "stop":
                ctrl.stop()
                print("ok")
            elif cmd == "status":
                print(json.dumps(ctrl.status().as_dict(), indent=2))
            elif cmd == "limits":
                lim = ctrl.limits()
                print(json.dumps({a.value: list(lim.limits[a]) for a in Axis}, indent=2))
            elif cmd == "snapshot":
                print(json.dumps(ctrl.snapshot(), indent=2))
            elif cmd == "calibration":
                print(json.dumps(ctrl.calibration.report(), indent=2))
            elif cmd == "self-test":
                print(json.dumps(ctrl.diagnostics.self_test(), indent=2))
            elif cmd == "repeatability":
                print(json.dumps(ctrl.diagnostics.repeatability_sweep().as_dict(), indent=2))
            elif cmd == "list-presets":
                print(json.dumps([p.as_dict() for p in ctrl.presets.all()], indent=2))
            elif cmd == "list-profiles":
                print(json.dumps(ctrl.planner.list_profiles(), indent=2))
            elif cmd == "move":
                ctrl.enable()
                ctrl.move(args.axis, args.steps, wait=not args.no_wait)
                print(json.dumps(ctrl.status().as_dict(), indent=2))
            elif cmd == "goto":
                ctrl.enable()
                ctrl.goto(args.axis, args.position, wait=not args.no_wait)
                print(json.dumps(ctrl.status().as_dict(), indent=2))
            elif cmd == "home":
                ctrl.enable()
                if args.axis:
                    ctrl.home(args.axis, wait=not args.no_wait)
                else:
                    ctrl.home_all(wait=not args.no_wait)
                print(json.dumps(ctrl.status().as_dict(), indent=2))
            elif cmd == "setspeed":
                ctrl.set_speed(args.axis, args.value)
                print("ok")
            elif cmd == "setacc":
                ctrl.set_acceleration(args.axis, args.value)
                print("ok")
            elif cmd == "profile":
                ctrl.apply_profile(args.name)
                print("ok")
            elif cmd == "preset":
                ctrl.apply_preset(args.name, wait=not args.no_wait)
                print(json.dumps(ctrl.status().as_dict(), indent=2))
            elif cmd == "sync":
                ctrl.enable()
                ctrl.sync_goto(
                    _parse_sync_targets(args.targets),
                    wait=not args.no_wait,
                    profile=args.profile,
                )
                print(json.dumps(ctrl.status().as_dict(), indent=2))
            elif cmd == "focus-um":
                ctrl.enable()
                ctrl.set_focus_um(args.micrometers, wait=not args.no_wait)
                print(json.dumps(ctrl.status().as_dict(), indent=2))
            elif cmd == "objective":
                ctrl.set_objective(args.name, wait=not args.no_wait)
                print(json.dumps(ctrl.status().as_dict(), indent=2))
            elif cmd == "illum-level":
                ctrl.set_illumination_level(args.level, wait=not args.no_wait)
                print(json.dumps(ctrl.status().as_dict(), indent=2))
            elif cmd == "zero":
                ctrl.zero(args.axis)
                print("ok")
            elif cmd == "jog":
                ctrl.enable()
                ctrl.jog(args.axis, fine=args.fine, wait=not args.no_wait)
                print(json.dumps(ctrl.status().as_dict(), indent=2))
            else:
                parser.error(f"Unknown command {cmd}")
                return 2
    except (
        ProtocolError,
        FileNotFoundError,
        ValueError,
        TimeoutError,
        OSError,
        ConfigError,
        SafetyError,
        CalibrationError,
        DiagnosticsError,
        MicroscopeControlError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
