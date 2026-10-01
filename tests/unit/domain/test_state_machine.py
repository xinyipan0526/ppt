import pytest

from ppt_agent.domain.state_machine import (
    AttemptEvent,
    AttemptStatus,
    GuardRejected,
    InvalidTransition,
    PageEvent,
    PageStatus,
    RunEvent,
    RunStatus,
    StepEvent,
    StepStatus,
    transition_attempt,
    transition_page,
    transition_run,
    transition_step,
)


def test_status_values_match_the_runtime_protocol() -> None:
    assert {status.value for status in RunStatus} == {
        "queued",
        "running",
        "waiting_for_user",
        "waiting_for_retry",
        "cancel_requested",
        "recovery_required",
        "succeeded",
        "failed",
        "cancelled",
    }
    assert {status.value for status in StepStatus} == {
        "pending",
        "running",
        "waiting_for_user",
        "waiting_for_retry",
        "recovery_required",
        "cancel_requested",
        "succeeded",
        "failed",
        "cancelled",
        "skipped",
    }
    assert {status.value for status in PageStatus} == {
        "planned",
        "queued",
        "running",
        "waiting_for_budget",
        "waiting_for_user",
        "waiting_for_retry",
        "recovery_required",
        "cancel_requested",
        "succeeded",
        "failed",
        "cancelled",
        "stale",
    }
    assert {status.value for status in AttemptStatus} == {
        "prepared",
        "submitted",
        "provider_running",
        "reconciling",
        "cancel_requested",
        "succeeded",
        "failed",
        "outcome_unknown",
        "cancelled",
    }


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        (RunStatus.QUEUED, RunEvent.DISPATCHED, RunStatus.RUNNING),
        (RunStatus.RUNNING, RunEvent.INTERRUPT_CREATED, RunStatus.WAITING_FOR_USER),
        (RunStatus.WAITING_FOR_USER, RunEvent.INTERRUPT_RESUMED, RunStatus.QUEUED),
        (RunStatus.RUNNING, RunEvent.PROCESS_RESTARTED, RunStatus.RECOVERY_REQUIRED),
        (RunStatus.RECOVERY_REQUIRED, RunEvent.RECOVERY_RETRY_SAFE, RunStatus.QUEUED),
        (RunStatus.CANCEL_REQUESTED, RunEvent.CANCELLATION_COMPLETED, RunStatus.CANCELLED),
    ],
)
def test_run_transitions(
    current: RunStatus, event: RunEvent, expected: RunStatus
) -> None:
    assert transition_run(current, event) is expected


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        (StepStatus.PENDING, StepEvent.STARTED, StepStatus.RUNNING),
        (StepStatus.RUNNING, StepEvent.COMPLETED, StepStatus.SUCCEEDED),
        (StepStatus.RUNNING, StepEvent.INTERRUPTED, StepStatus.WAITING_FOR_USER),
        (StepStatus.WAITING_FOR_USER, StepEvent.INTERRUPT_RESUMED, StepStatus.PENDING),
        (StepStatus.RUNNING, StepEvent.PROCESS_RESTARTED, StepStatus.RECOVERY_REQUIRED),
        (StepStatus.PENDING, StepEvent.DEPENDENCY_NOT_SELECTED, StepStatus.SKIPPED),
    ],
)
def test_step_transitions(
    current: StepStatus, event: StepEvent, expected: StepStatus
) -> None:
    assert transition_step(current, event) is expected


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        (PageStatus.PLANNED, PageEvent.APPROVED, PageStatus.QUEUED),
        (PageStatus.PLANNED, PageEvent.CANCEL_REQUESTED, PageStatus.CANCEL_REQUESTED),
        (PageStatus.QUEUED, PageEvent.STARTED, PageStatus.RUNNING),
        (PageStatus.RUNNING, PageEvent.RETRY_SCHEDULED, PageStatus.WAITING_FOR_RETRY),
        (PageStatus.WAITING_FOR_RETRY, PageEvent.RETRY_DUE, PageStatus.QUEUED),
        (PageStatus.RUNNING, PageEvent.BUDGET_RESERVATION_DENIED, PageStatus.WAITING_FOR_BUDGET),
        (PageStatus.SUCCEEDED, PageEvent.UPSTREAM_INVALIDATED, PageStatus.STALE),
        (PageStatus.STALE, PageEvent.REGENERATION_REQUESTED, PageStatus.QUEUED),
        (PageStatus.FAILED, PageEvent.MANUAL_RETRY_REQUESTED, PageStatus.QUEUED),
    ],
)
def test_page_transitions(
    current: PageStatus, event: PageEvent, expected: PageStatus
) -> None:
    assert transition_page(current, event) is expected


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        (None, AttemptEvent.PREPARED, AttemptStatus.PREPARED),
        (AttemptStatus.PREPARED, AttemptEvent.SUBMISSION_INTENT_COMMITTED, AttemptStatus.SUBMITTED),
        (AttemptStatus.SUBMITTED, AttemptEvent.PROVIDER_JOB_ACCEPTED, AttemptStatus.PROVIDER_RUNNING),
        (AttemptStatus.SUBMITTED, AttemptEvent.RECOVERY_STARTED, AttemptStatus.RECONCILING),
        (AttemptStatus.RECONCILING, AttemptEvent.OUTCOME_CANNOT_BE_PROVEN, AttemptStatus.OUTCOME_UNKNOWN),
        (AttemptStatus.CANCEL_REQUESTED, AttemptEvent.PROVIDER_CANCEL_CONFIRMED, AttemptStatus.CANCELLED),
    ],
)
def test_attempt_transitions(
    current: AttemptStatus | None,
    event: AttemptEvent,
    expected: AttemptStatus,
) -> None:
    assert transition_attempt(current, event) is expected


def test_illegal_transition_is_rejected() -> None:
    with pytest.raises(InvalidTransition, match="run: succeeded --run_dispatched"):
        transition_run(RunStatus.SUCCEEDED, RunEvent.DISPATCHED)


def test_unsatisfied_guard_is_rejected_before_state_changes() -> None:
    with pytest.raises(GuardRejected, match="page: planned --page_approved"):
        transition_page(
            PageStatus.PLANNED,
            PageEvent.APPROVED,
            guard_satisfied=False,
        )
