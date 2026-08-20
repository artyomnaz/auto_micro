from microscope_control.axes import Axis
from microscope_control.config import load_config
from microscope_control.transport.firmware_runtime import FirmwareRuntime


def test_firmware_runtime_goto_and_limits():
    cfg = load_config()
    rt = FirmwareRuntime(cfg)

    assert rt.handle_line("PING") == "ok"
    assert rt.handle_line("VERSION").startswith("version:")
    assert rt.handle_line("GOTO F 10").startswith("error:")

    assert rt.handle_line("ENABLE") == "ok"
    assert rt.handle_line("GOTO F 1000") == "ok"
    for _ in range(50):
        rt.tick(0.05)
    assert rt.state.axes[Axis.FOCUS].position == 1000

    assert rt.handle_line("GOTO F 999999").startswith("error:soft limit")
    assert rt.handle_line("HOME F") == "ok"
    for _ in range(80):
        rt.tick(0.05)
    assert rt.state.axes[Axis.FOCUS].position == 0


def test_sync_and_setpos():
    rt = FirmwareRuntime(load_config())
    rt.handle_line("ENABLE")
    assert rt.handle_line("SYNC F=100 M=200 I=300") == "ok"
    for _ in range(100):
        rt.tick(0.05)
    assert rt.state.axes[Axis.FOCUS].position == 100
    assert rt.state.axes[Axis.MAGNIFICATION].position == 200
    assert rt.state.axes[Axis.ILLUMINATION].position == 300
    assert rt.handle_line("SETPOS F 0") == "ok"
    assert rt.state.axes[Axis.FOCUS].position == 0
    assert rt.handle_line("LIMITS").startswith("limits:")


def test_illumination_soft_limit_zero_floor():
    rt = FirmwareRuntime(load_config())
    rt.handle_line("ENABLE")
    assert rt.handle_line("MOVE I -1").startswith("error:soft limit")
