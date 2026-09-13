"""Optional, fault-tolerant execution telemetry contracts."""

from typing import Protocol


class TelemetryHook(Protocol):
    def emit(self, event: dict) -> None:
        """Observe an execution event without participating in its control flow."""


class NullTelemetryHook:
    """Default no-op hook: no logging, network activity, or exceptions."""

    def emit(self, event: dict) -> None:
        return None
