from microscope_control.axes import Axis
from microscope_control.config import load_config
from microscope_control.controller import MicroscopeController
from microscope_control.exceptions import SafetyError
import pytest


def _fast_cfg():
    cfg = load_config()
    cfg.setdefault("controller", {})["poll_interval_s"] = 0.01
    cfg.setdefault("safety", {})["settle_delay_s"] = 0.0
    cfg.setdefault("diagnostics", {})["self_test_on_open"] = False
    for axis in ("F", "M", "I"):
        cfg["axes"][axis]["default_speed_sps"] = 20000
        cfg["axes"][axis]["max_speed_sps"] = 50000
    return cfg


def test_controller_goto_wait():
    with MicroscopeController(config=_fast_cfg()) as ctrl:
        ctrl.ping()
        ver = ctrl.version()
        assert ver.protocol_version == "1.1"
        ctrl.enable()
        st = ctrl.goto(Axis.FOCUS, 500)
        assert st.moving is False
        assert st.position(Axis.FOCUS) == 500

        st = ctrl.move(Axis.FOCUS, 250)
        assert st.position(Axis.FOCUS) == 750

        st = ctrl.home(Axis.FOCUS)
        assert st.position(Axis.FOCUS) == 0

        with pytest.raises(SafetyError, match="soft limits"):
            ctrl.goto(Axis.ILLUMINATION, -5)


def test_focus_unit_helpers_and_preset():
    cfg = _fast_cfg()
    with MicroscopeController(config=cfg) as ctrl:
        assert ctrl.steps_per_um() == pytest.approx(3.2)
        assert ctrl.focus_um_to_steps(10.0) == 32
        assert ctrl.focus_steps_to_um(32) == pytest.approx(10.0)

        ctrl.apply_preset("brightfield_10x")
        st = ctrl.status()
        assert st.position(Axis.MAGNIFICATION) == 3200
        assert st.position(Axis.ILLUMINATION) == 12800


def test_sync_objective_illum_and_safety():
    with MicroscopeController(config=_fast_cfg()) as ctrl:
        ctrl.enable()
        ctrl.sync_goto({Axis.FOCUS: 10, Axis.MAGNIFICATION: 20, Axis.ILLUMINATION: 30})
        st = ctrl.status()
        assert st.position(Axis.FOCUS) == 10
        assert st.position(Axis.MAGNIFICATION) == 20
        assert st.position(Axis.ILLUMINATION) == 30

        ctrl.set_objective("40x")
        assert ctrl.status().position(Axis.MAGNIFICATION) == 6400

        ctrl.set_illumination_level(50)
        assert ctrl.status().position(Axis.ILLUMINATION) == 16000

        with pytest.raises(SafetyError):
            ctrl.move(Axis.FOCUS, 999999)


def test_repeatability_open_loop():
    cfg = _fast_cfg()
    cfg["diagnostics"]["repeatability"] = {
        "axis": "F",
        "amplitude_steps": 100,
        "cycles": 2,
        "settle_s": 0.0,
    }
    with MicroscopeController(config=cfg) as ctrl:
        result = ctrl.diagnostics.repeatability_sweep()
        assert result.ok
        assert result.residual_steps == 0
