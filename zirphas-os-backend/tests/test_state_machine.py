from dataclasses import replace

import pytest

from app.core.os_state_machine import InvalidTransition, StateContext, transition_state
from app.models.enums import OrderStatus as S


READY = StateContext(
    has_device_identity=True, has_physical_checklist=True, has_technician=True,
    has_technical_report=True, quote_is_approved=True, stock_available=True,
    fully_paid=True,
)


@pytest.mark.parametrize("source,target", [
    (S.INTAKE, S.ANALYZING), (S.ANALYZING, S.AWAITING_APPROVAL),
    (S.AWAITING_PARTS, S.REPAIRING), (S.REPAIRING, S.COMPLETED),
    (S.COMPLETED, S.DELIVERED),
])
def test_normal_lifecycle(source, target):
    assert transition_state(source, target, READY) == target


@pytest.mark.parametrize("source,target", [
    (S.INTAKE, S.COMPLETED), (S.INTAKE, S.REPAIRING),
    (S.ANALYZING, S.REPAIRING), (S.REPAIRING, S.DELIVERED),
    (S.COMPLETED, S.REPAIRING), (S.DELIVERED, S.COMPLETED),
    (S.COMPLETED, S.COMPLETED), (S.DELIVERED, S.DELIVERED),
])
def test_skipped_reversed_and_repeated_transitions_are_rejected(source, target):
    with pytest.raises(InvalidTransition):
        transition_state(source, target, READY)


@pytest.mark.parametrize("source,target,missing", [
    (S.INTAKE, S.ANALYZING, "has_device_identity"),
    (S.INTAKE, S.ANALYZING, "has_physical_checklist"),
    (S.INTAKE, S.ANALYZING, "has_technician"),
    (S.ANALYZING, S.AWAITING_APPROVAL, "has_technical_report"),
    (S.ANALYZING, S.AWAITING_APPROVAL, "has_technician"),
    (S.AWAITING_PARTS, S.REPAIRING, "stock_available"),
    (S.AWAITING_PARTS, S.REPAIRING, "quote_is_approved"),
    (S.REPAIRING, S.COMPLETED, "stock_available"),
    (S.REPAIRING, S.COMPLETED, "quote_is_approved"),
    (S.REPAIRING, S.COMPLETED, "has_technical_report"),
    (S.COMPLETED, S.DELIVERED, "fully_paid"),
])
def test_guards_block_invalid_progress(source, target, missing):
    with pytest.raises(InvalidTransition):
        transition_state(source, target, replace(READY, **{missing: False}))


def test_approval_requires_an_explicit_decision():
    with pytest.raises(InvalidTransition):
        transition_state(S.AWAITING_APPROVAL, S.REPAIRING, READY)
    context = replace(READY, approval_decision=True)
    assert transition_state(S.AWAITING_APPROVAL, S.REPAIRING, context) == S.REPAIRING


def test_approval_with_missing_parts_enters_waiting_state():
    context = replace(READY, approval_decision=True, stock_available=False)
    assert transition_state(S.AWAITING_APPROVAL, S.AWAITING_PARTS, context) == S.AWAITING_PARTS
    with pytest.raises(InvalidTransition):
        transition_state(S.AWAITING_APPROVAL, S.REPAIRING, context)


def test_available_parts_cannot_enter_waiting_state():
    with pytest.raises(InvalidTransition):
        transition_state(S.AWAITING_APPROVAL, S.AWAITING_PARTS, replace(
            READY, approval_decision=True
        ))


def test_refusal_returns_to_analysis_only_with_explicit_refusal():
    assert transition_state(S.AWAITING_APPROVAL, S.ANALYZING, replace(
        READY, approval_decision=False
    )) == S.ANALYZING
    for decision in (None, True):
        with pytest.raises(InvalidTransition):
            transition_state(S.AWAITING_APPROVAL, S.ANALYZING, replace(
                READY, approval_decision=decision
            ))
