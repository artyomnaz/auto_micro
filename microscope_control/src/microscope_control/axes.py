"""Axis identifiers for the three motorized microscope controls."""

from __future__ import annotations

from enum import Enum


class Axis(str, Enum):
    FOCUS = "F"
    MAGNIFICATION = "M"
    ILLUMINATION = "I"

    @classmethod
    def parse(cls, value: str) -> Axis:
        key = value.strip().upper()
        aliases = {
            "F": cls.FOCUS,
            "FOCUS": cls.FOCUS,
            "Z": cls.FOCUS,
            "M": cls.MAGNIFICATION,
            "MAG": cls.MAGNIFICATION,
            "MAGNIFICATION": cls.MAGNIFICATION,
            "I": cls.ILLUMINATION,
            "ILLUM": cls.ILLUMINATION,
            "ILLUMINATION": cls.ILLUMINATION,
        }
        if key not in aliases:
            raise ValueError(f"Unknown axis {value!r}; expected F, M, or I")
        return aliases[key]
