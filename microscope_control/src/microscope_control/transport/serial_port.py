"""USB serial transport to Arduino UNO R3."""

from __future__ import annotations

import time
from typing import Any

try:
    import serial
except ImportError:  # pragma: no cover
    serial = None  # type: ignore[assignment]


class SerialPort:
    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout_s: float = 2.0,
        boot_delay_s: float = 2.0,
    ) -> None:
        if serial is None:
            raise ImportError("pyserial is required for SerialPort; pip install pyserial")
        self.port = port
        self.baudrate = baudrate
        self.timeout_s = timeout_s
        self.boot_delay_s = boot_delay_s
        self._ser: Any | None = None

    def open(self) -> None:
        if self._ser is not None and self._ser.is_open:
            return
        self._ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout_s,
            write_timeout=self.timeout_s,
        )
        # UNO resets on open; wait for firmware ready banner / settle.
        time.sleep(self.boot_delay_s)
        self.reset_input()

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    def is_open(self) -> bool:
        return self._ser is not None and bool(self._ser.is_open)

    def write_line(self, line: str) -> None:
        if not self.is_open():
            raise RuntimeError("SerialPort is not open")
        payload = (line.rstrip("\r\n") + "\n").encode("ascii", errors="strict")
        self._ser.write(payload)
        self._ser.flush()

    def read_line(self) -> str:
        if not self.is_open():
            raise RuntimeError("SerialPort is not open")
        raw = self._ser.readline()
        if not raw:
            raise TimeoutError(f"No response on {self.port} within {self.timeout_s}s")
        return raw.decode("ascii", errors="replace").strip()

    def reset_input(self) -> None:
        if self.is_open():
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
