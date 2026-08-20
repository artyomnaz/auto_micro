"""Named acquisition presets (return to predefined settings)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from microscope_control.axes import Axis
from microscope_control.exceptions import ConfigError


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    positions: dict[Axis, int]
    profile: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "profile": self.profile,
            "positions": {axis.value: pos for axis, pos in self.positions.items()},
        }


class PresetLibrary:
    def __init__(self, config: dict[str, Any]) -> None:
        self._presets: dict[str, Preset] = {}
        for name, body in (config.get("presets") or {}).items():
            if not isinstance(body, dict):
                continue
            positions: dict[Axis, int] = {}
            for axis in Axis:
                if axis.value in body:
                    positions[axis] = int(body[axis.value])
            if not positions:
                continue
            self._presets[name] = Preset(
                name=name,
                description=str(body.get("description", "")),
                positions=positions,
                profile=body.get("profile"),
            )

    def list(self) -> list[str]:
        return sorted(self._presets)

    def get(self, name: str) -> Preset:
        if name not in self._presets:
            known = ", ".join(self.list()) or "(none)"
            raise ConfigError(f"Unknown preset {name!r}; known: {known}")
        return self._presets[name]

    def all(self) -> list[Preset]:
        return [self._presets[n] for n in self.list()]
