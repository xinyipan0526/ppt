"""Domain contracts for durable workflow event projection."""

from enum import StrEnum


class EventDurability(StrEnum):
    """Retention class for a persisted workflow event."""

    CORE = "core"
    EPHEMERAL = "ephemeral"

