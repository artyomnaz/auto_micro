"""Transport abstractions for talking to the controller firmware."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Transport(Protocol):
    def open(self) -> None: ...

    def close(self) -> None: ...

    def is_open(self) -> bool: ...

    def write_line(self, line: str) -> None: ...

    def read_line(self) -> str: ...

    def reset_input(self) -> None: ...
