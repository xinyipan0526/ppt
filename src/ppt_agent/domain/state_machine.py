"""Canonical workflow state names and pure transition rules.

This module contains no persistence or side effects. Database services use these
tables as the single source of truth before applying transactional side effects.
"""

from enum import StrEnum
from typing import TypeVar


class InvalidTransition(ValueError):
    """Raised when an event is not valid for the current state."""


class GuardRejected(ValueError):
    """Raised when a transition's caller-provided guard is not satisfied."""


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    WAITING_FOR_RETRY = "waiting_for_retry"
    CANCEL_REQUESTED = "cancel_requested"
    RECOVERY_REQUIRED = "recovery_required"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunEvent(StrEnum):
    DISPATCHED = "run_dispatched"
    INTERRUPT_CREATED = "interrupt_created"
    INTERRUPT_RESUMED = "interrupt_resumed"
    RETRY_SCHEDULED = "retry_scheduled"
    RETRY_DUE = "retry_due"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLATION_COMPLETED = "cancellation_completed"
    ALL_REQUIRED_STEPS_SUCCEEDED = "all_required_steps_succeeded"
    UNRECOVERABLE_FAILURE = "unrecoverable_failure"
    PROCESS_RESTARTED = "process_restarted"
    RECOVERY_RETRY_SAFE = "recovery_retry_safe"
    RECOVERY_USER_INPUT_REQUIRED = "recovery_user_input_required"
    RECOVERY_CANCEL_REQUIRED = "recovery_cancel_required"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    WAITING_FOR_RETRY = "waiting_for_retry"
    RECOVERY_REQUIRED = "recovery_required"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class StepEvent(StrEnum):
    STARTED = "step_started"
    COMPLETED = "Completed"
    INTERRUPTED = "Interrupted"
    INTERRUPT_RESUMED = "interrupt_resumed"
    RETRY_SCHEDULED = "RetryScheduled"
    RETRY_DUE = "retry_due"
    RECOVERY_REQUIRED = "RecoveryRequired"
    RECOVERY_RETRY_SAFE = "step_recovery_retry_safe"
    RECOVERY_USER_INPUT_REQUIRED = "step_recovery_user_input_required"
    RECOVERY_FAILED = "step_recovery_failed"
    FAILED = "Failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLATION_COMPLETED = "step_cancellation_completed"
    DEPENDENCY_NOT_SELECTED = "dependency_not_selected"
    PROCESS_RESTARTED = "process_restarted"


class PageStatus(StrEnum):
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_BUDGET = "waiting_for_budget"
    WAITING_FOR_USER = "waiting_for_user"
    WAITING_FOR_RETRY = "waiting_for_retry"
    RECOVERY_REQUIRED = "recovery_required"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"


class PageEvent(StrEnum):
    APPROVED = "page_approved"
    STARTED = "page_started"
    BUDGET_RESERVATION_DENIED = "budget_reservation_denied"
    BUDGET_INTERRUPT_RESUMED = "budget_interrupt_resumed"
    INTERRUPT_CREATED = "page_interrupt_created"
    INTERRUPT_RESUMED = "page_interrupt_resumed"
    RETRY_SCHEDULED = "page_retry_scheduled"
    RETRY_DUE = "page_retry_due"
    COMPLETED = "page_completed"
    UPSTREAM_INVALIDATED = "upstream_invalidated"
    REGENERATION_REQUESTED = "regeneration_requested"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLATION_COMPLETED = "page_cancellation_completed"
    FAILED = "page_failed"
    MANUAL_RETRY_REQUESTED = "manual_retry_requested"
    PROCESS_RESTARTED = "process_restarted"
    RECOVERY_RETRY_SAFE = "page_recovery_retry_safe"
    RECOVERY_USER_INPUT_REQUIRED = "page_recovery_user_input_required"
    RECOVERY_FAILED = "page_recovery_failed"


class AttemptStatus(StrEnum):
    PREPARED = "prepared"
    SUBMITTED = "submitted"
    PROVIDER_RUNNING = "provider_running"
    RECONCILING = "reconciling"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    CANCELLED = "cancelled"


class AttemptEvent(StrEnum):
    PREPARED = "attempt_prepared"
    SUBMISSION_INTENT_COMMITTED = "submission_intent_committed"
    SUBMISSION_ABORTED = "submission_aborted"
    PROVIDER_JOB_ACCEPTED = "provider_job_accepted"
    RESULT_RECEIVED = "result_received"
    KNOWN_FAILURE = "known_failure"
    RECOVERY_STARTED = "recovery_started"
    RECONCILE_SUCCESS_FOUND = "reconcile_success_found"
    RECONCILE_FAILURE_FOUND = "reconcile_failure_found"
    OUTCOME_CANNOT_BE_PROVEN = "outcome_cannot_be_proven"
    CANCEL_REQUESTED = "cancel_requested"
    PROVIDER_CANCEL_CONFIRMED = "provider_cancel_confirmed"
    CANCEL_OUTCOME_UNKNOWN = "cancel_outcome_unknown"


RUN_TRANSITIONS: dict[tuple[RunStatus, RunEvent], RunStatus] = {
    (RunStatus.QUEUED, RunEvent.DISPATCHED): RunStatus.RUNNING,
    (RunStatus.RUNNING, RunEvent.INTERRUPT_CREATED): RunStatus.WAITING_FOR_USER,
    (RunStatus.WAITING_FOR_USER, RunEvent.INTERRUPT_RESUMED): RunStatus.QUEUED,
    (RunStatus.RUNNING, RunEvent.RETRY_SCHEDULED): RunStatus.WAITING_FOR_RETRY,
    (RunStatus.WAITING_FOR_RETRY, RunEvent.RETRY_DUE): RunStatus.QUEUED,
    (RunStatus.CANCEL_REQUESTED, RunEvent.CANCELLATION_COMPLETED): RunStatus.CANCELLED,
    (RunStatus.RUNNING, RunEvent.ALL_REQUIRED_STEPS_SUCCEEDED): RunStatus.SUCCEEDED,
    (RunStatus.RUNNING, RunEvent.UNRECOVERABLE_FAILURE): RunStatus.FAILED,
    (RunStatus.RECOVERY_REQUIRED, RunEvent.UNRECOVERABLE_FAILURE): RunStatus.FAILED,
    (RunStatus.RUNNING, RunEvent.PROCESS_RESTARTED): RunStatus.RECOVERY_REQUIRED,
    (RunStatus.CANCEL_REQUESTED, RunEvent.PROCESS_RESTARTED): RunStatus.CANCEL_REQUESTED,
    (RunStatus.RECOVERY_REQUIRED, RunEvent.RECOVERY_RETRY_SAFE): RunStatus.QUEUED,
    (
        RunStatus.RECOVERY_REQUIRED,
        RunEvent.RECOVERY_USER_INPUT_REQUIRED,
    ): RunStatus.WAITING_FOR_USER,
    (
        RunStatus.RECOVERY_REQUIRED,
        RunEvent.RECOVERY_CANCEL_REQUIRED,
    ): RunStatus.CANCEL_REQUESTED,
}
for _run_status in (
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.WAITING_FOR_USER,
    RunStatus.WAITING_FOR_RETRY,
    RunStatus.RECOVERY_REQUIRED,
):
    RUN_TRANSITIONS[(_run_status, RunEvent.CANCEL_REQUESTED)] = RunStatus.CANCEL_REQUESTED


STEP_TRANSITIONS: dict[tuple[StepStatus, StepEvent], StepStatus] = {
    (StepStatus.PENDING, StepEvent.STARTED): StepStatus.RUNNING,
    (StepStatus.RUNNING, StepEvent.COMPLETED): StepStatus.SUCCEEDED,
    (StepStatus.RUNNING, StepEvent.INTERRUPTED): StepStatus.WAITING_FOR_USER,
    (StepStatus.WAITING_FOR_USER, StepEvent.INTERRUPT_RESUMED): StepStatus.PENDING,
    (StepStatus.RUNNING, StepEvent.RETRY_SCHEDULED): StepStatus.WAITING_FOR_RETRY,
    (StepStatus.WAITING_FOR_RETRY, StepEvent.RETRY_DUE): StepStatus.PENDING,
    (StepStatus.RUNNING, StepEvent.RECOVERY_REQUIRED): StepStatus.RECOVERY_REQUIRED,
    (StepStatus.RECOVERY_REQUIRED, StepEvent.RECOVERY_RETRY_SAFE): StepStatus.PENDING,
    (
        StepStatus.RECOVERY_REQUIRED,
        StepEvent.RECOVERY_USER_INPUT_REQUIRED,
    ): StepStatus.WAITING_FOR_USER,
    (StepStatus.RECOVERY_REQUIRED, StepEvent.RECOVERY_FAILED): StepStatus.FAILED,
    (StepStatus.RUNNING, StepEvent.FAILED): StepStatus.FAILED,
    (StepStatus.RUNNING, StepEvent.CANCEL_REQUESTED): StepStatus.CANCEL_REQUESTED,
    (
        StepStatus.CANCEL_REQUESTED,
        StepEvent.CANCELLATION_COMPLETED,
    ): StepStatus.CANCELLED,
    (StepStatus.PENDING, StepEvent.DEPENDENCY_NOT_SELECTED): StepStatus.SKIPPED,
    (StepStatus.RUNNING, StepEvent.PROCESS_RESTARTED): StepStatus.RECOVERY_REQUIRED,
}
for _step_status in (
    StepStatus.PENDING,
    StepStatus.WAITING_FOR_USER,
    StepStatus.WAITING_FOR_RETRY,
    StepStatus.RECOVERY_REQUIRED,
):
    STEP_TRANSITIONS[(_step_status, StepEvent.CANCEL_REQUESTED)] = StepStatus.CANCELLED


PAGE_TRANSITIONS: dict[tuple[PageStatus, PageEvent], PageStatus] = {
    (PageStatus.PLANNED, PageEvent.APPROVED): PageStatus.QUEUED,
    (PageStatus.QUEUED, PageEvent.STARTED): PageStatus.RUNNING,
    (
        PageStatus.RUNNING,
        PageEvent.BUDGET_RESERVATION_DENIED,
    ): PageStatus.WAITING_FOR_BUDGET,
    (
        PageStatus.WAITING_FOR_BUDGET,
        PageEvent.BUDGET_INTERRUPT_RESUMED,
    ): PageStatus.QUEUED,
    (PageStatus.RUNNING, PageEvent.INTERRUPT_CREATED): PageStatus.WAITING_FOR_USER,
    (PageStatus.WAITING_FOR_USER, PageEvent.INTERRUPT_RESUMED): PageStatus.QUEUED,
    (PageStatus.RUNNING, PageEvent.RETRY_SCHEDULED): PageStatus.WAITING_FOR_RETRY,
    (PageStatus.WAITING_FOR_RETRY, PageEvent.RETRY_DUE): PageStatus.QUEUED,
    (PageStatus.RUNNING, PageEvent.COMPLETED): PageStatus.SUCCEEDED,
    (PageStatus.SUCCEEDED, PageEvent.UPSTREAM_INVALIDATED): PageStatus.STALE,
    (PageStatus.STALE, PageEvent.REGENERATION_REQUESTED): PageStatus.QUEUED,
    (
        PageStatus.CANCEL_REQUESTED,
        PageEvent.CANCELLATION_COMPLETED,
    ): PageStatus.CANCELLED,
    (PageStatus.RUNNING, PageEvent.FAILED): PageStatus.FAILED,
    (PageStatus.FAILED, PageEvent.MANUAL_RETRY_REQUESTED): PageStatus.QUEUED,
    (PageStatus.RUNNING, PageEvent.PROCESS_RESTARTED): PageStatus.RECOVERY_REQUIRED,
    (PageStatus.CANCEL_REQUESTED, PageEvent.PROCESS_RESTARTED): PageStatus.CANCEL_REQUESTED,
    (
        PageStatus.RECOVERY_REQUIRED,
        PageEvent.RECOVERY_RETRY_SAFE,
    ): PageStatus.QUEUED,
    (
        PageStatus.RECOVERY_REQUIRED,
        PageEvent.RECOVERY_USER_INPUT_REQUIRED,
    ): PageStatus.WAITING_FOR_USER,
    (PageStatus.RECOVERY_REQUIRED, PageEvent.RECOVERY_FAILED): PageStatus.FAILED,
}
for _page_status in (
    PageStatus.PLANNED,
    PageStatus.QUEUED,
    PageStatus.RUNNING,
    PageStatus.WAITING_FOR_BUDGET,
    PageStatus.WAITING_FOR_USER,
    PageStatus.WAITING_FOR_RETRY,
    PageStatus.RECOVERY_REQUIRED,
    PageStatus.STALE,
):
    PAGE_TRANSITIONS[(_page_status, PageEvent.CANCEL_REQUESTED)] = PageStatus.CANCEL_REQUESTED


ATTEMPT_TRANSITIONS: dict[
    tuple[AttemptStatus | None, AttemptEvent], AttemptStatus
] = {
    (None, AttemptEvent.PREPARED): AttemptStatus.PREPARED,
    (
        AttemptStatus.PREPARED,
        AttemptEvent.SUBMISSION_INTENT_COMMITTED,
    ): AttemptStatus.SUBMITTED,
    (AttemptStatus.PREPARED, AttemptEvent.SUBMISSION_ABORTED): AttemptStatus.FAILED,
    (
        AttemptStatus.SUBMITTED,
        AttemptEvent.PROVIDER_JOB_ACCEPTED,
    ): AttemptStatus.PROVIDER_RUNNING,
    (AttemptStatus.SUBMITTED, AttemptEvent.RESULT_RECEIVED): AttemptStatus.SUCCEEDED,
    (
        AttemptStatus.PROVIDER_RUNNING,
        AttemptEvent.RESULT_RECEIVED,
    ): AttemptStatus.SUCCEEDED,
    (AttemptStatus.SUBMITTED, AttemptEvent.KNOWN_FAILURE): AttemptStatus.FAILED,
    (AttemptStatus.PROVIDER_RUNNING, AttemptEvent.KNOWN_FAILURE): AttemptStatus.FAILED,
    (AttemptStatus.SUBMITTED, AttemptEvent.RECOVERY_STARTED): AttemptStatus.RECONCILING,
    (
        AttemptStatus.PROVIDER_RUNNING,
        AttemptEvent.RECOVERY_STARTED,
    ): AttemptStatus.RECONCILING,
    (
        AttemptStatus.RECONCILING,
        AttemptEvent.RECONCILE_SUCCESS_FOUND,
    ): AttemptStatus.SUCCEEDED,
    (
        AttemptStatus.RECONCILING,
        AttemptEvent.RECONCILE_FAILURE_FOUND,
    ): AttemptStatus.FAILED,
    (
        AttemptStatus.RECONCILING,
        AttemptEvent.OUTCOME_CANNOT_BE_PROVEN,
    ): AttemptStatus.OUTCOME_UNKNOWN,
    (
        AttemptStatus.CANCEL_REQUESTED,
        AttemptEvent.PROVIDER_CANCEL_CONFIRMED,
    ): AttemptStatus.CANCELLED,
    (
        AttemptStatus.CANCEL_REQUESTED,
        AttemptEvent.CANCEL_OUTCOME_UNKNOWN,
    ): AttemptStatus.OUTCOME_UNKNOWN,
}
for _attempt_status in (
    AttemptStatus.SUBMITTED,
    AttemptStatus.PROVIDER_RUNNING,
    AttemptStatus.RECONCILING,
):
    ATTEMPT_TRANSITIONS[
        (_attempt_status, AttemptEvent.CANCEL_REQUESTED)
    ] = AttemptStatus.CANCEL_REQUESTED


StatusT = TypeVar("StatusT", bound=StrEnum)
EventT = TypeVar("EventT", bound=StrEnum)


def _transition(
    entity: str,
    current: StatusT | None,
    event: EventT,
    transitions: dict[tuple[StatusT | None, EventT], StatusT],
    *,
    guard_satisfied: bool,
) -> StatusT:
    current_value = "missing" if current is None else current.value
    description = f"{entity}: {current_value} --{event.value}"
    if not guard_satisfied:
        raise GuardRejected(description)
    try:
        return transitions[(current, event)]
    except KeyError as exc:
        raise InvalidTransition(description) from exc


def transition_run(
    current: RunStatus,
    event: RunEvent,
    *,
    guard_satisfied: bool = True,
) -> RunStatus:
    return _transition(
        "run", current, event, RUN_TRANSITIONS, guard_satisfied=guard_satisfied
    )


def transition_step(
    current: StepStatus,
    event: StepEvent,
    *,
    guard_satisfied: bool = True,
) -> StepStatus:
    return _transition(
        "step", current, event, STEP_TRANSITIONS, guard_satisfied=guard_satisfied
    )


def transition_page(
    current: PageStatus,
    event: PageEvent,
    *,
    guard_satisfied: bool = True,
) -> PageStatus:
    return _transition(
        "page", current, event, PAGE_TRANSITIONS, guard_satisfied=guard_satisfied
    )


def transition_attempt(
    current: AttemptStatus | None,
    event: AttemptEvent,
    *,
    guard_satisfied: bool = True,
) -> AttemptStatus:
    return _transition(
        "attempt",
        current,
        event,
        ATTEMPT_TRANSITIONS,
        guard_satisfied=guard_satisfied,
    )
