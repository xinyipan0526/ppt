"""Provider operation aggregate states."""

from enum import StrEnum


class OperationAggregateStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    CANCELLED = "cancelled"

