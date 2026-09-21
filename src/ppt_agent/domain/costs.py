"""Cost ledger domain states."""

from enum import StrEnum


class ReservationStatus(StrEnum):
    ACTIVE = "active"
    SETTLED = "settled"
    RELEASED = "released"
    PARTIALLY_SETTLED = "partially_settled"
    HELD_UNKNOWN = "held_unknown"

