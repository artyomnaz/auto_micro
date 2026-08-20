"""Exceptions for the microscope control stack."""

from __future__ import annotations


class MicroscopeControlError(RuntimeError):
    """Base error for host-side control failures."""


class ConfigError(MicroscopeControlError):
    """Invalid or incomplete configuration."""


class SafetyError(MicroscopeControlError):
    """Motion rejected by safety policy."""


class CalibrationError(MicroscopeControlError):
    """Unit conversion / calibration failure."""


class DiagnosticsError(MicroscopeControlError):
    """Self-test or diagnostics failure."""
