from microscope_control.axes import Axis
from microscope_control.config import load_config, validate_config
from microscope_control.calibration import CalibrationService
from microscope_control.exceptions import ConfigError, SafetyError
from microscope_control.protocol import (
    encode_goto,
    encode_move,
    encode_status,
    encode_sync,
    encode_version,
    ensure_ok,
    format_status,
    parse_status,
    parse_version,
    ProtocolError,
)
from microscope_control.safety import SafetyGuard
import pytest


def test_encode_commands():
    assert encode_move(Axis.FOCUS, -10) == "MOVE F -10"
    assert encode_goto(Axis.ILLUMINATION, 100) == "GOTO I 100"
    assert encode_status() == "STATUS"
    assert encode_sync({Axis.FOCUS: 1, Axis.MAGNIFICATION: 2}) == "SYNC F=1 M=2"
    assert encode_version() == "VERSION"


def test_parse_status_roundtrip():
    line = format_status(
        enabled=True,
        moving=False,
        positions={Axis.FOCUS: 1, Axis.MAGNIFICATION: 2, Axis.ILLUMINATION: 3},
        targets={Axis.FOCUS: 1, Axis.MAGNIFICATION: 2, Axis.ILLUMINATION: 3},
        speeds={Axis.FOCUS: 800.0, Axis.MAGNIFICATION: 600.0, Axis.ILLUMINATION: 400.0},
        accels={Axis.FOCUS: 1600.0, Axis.MAGNIFICATION: 1200.0, Axis.ILLUMINATION: 800.0},
        microstep=16,
        profile="fine",
    )
    st = parse_status(line)
    assert st.enabled is True
    assert st.moving is False
    assert st.position(Axis.FOCUS) == 1
    assert st.microstep == 16
    assert st.profile == "fine"


def test_parse_version():
    ver = parse_version("version:id=microscope_controller,proto=1.1,axes=F/M/I,ms=16")
    assert ver.firmware_id == "microscope_controller"
    assert ver.protocol_version == "1.1"
    assert ver.extra["ms"] == "16"


def test_ensure_ok_and_error():
    ensure_ok("ok")
    with pytest.raises(ProtocolError, match="soft limit"):
        ensure_ok("error:soft limit")


def test_config_loads_with_includes():
    cfg = load_config()
    assert "ultra_fine" in (cfg.get("motion_profiles") or {})
    assert cfg["controller"]["poll_interval_s"] == 0.015
    validate_config(cfg)


def test_calibration_focus_resolution():
    cfg = load_config()
    cal = CalibrationService(cfg)
    assert cal.focus.um_per_microstep == pytest.approx(0.3125)
    assert cal.focus.um_to_steps(10.0) == 32
    assert cal.magnification.named_position("10x") == 3200
    assert cal.illumination.level_to_steps(0) == 0
    assert cal.illumination.level_to_steps(100) == 32000


def test_safety_relative_cap():
    cfg = load_config()
    guard = SafetyGuard(cfg)
    with pytest.raises(SafetyError):
        guard.check_relative(Axis.FOCUS, 0, 999999)
