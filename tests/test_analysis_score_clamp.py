"""Prompt-injection hardening: the LLM match score is clamped server-side to 0-100.

A candidate controls their CV text; even with the _INJECTION_GUARD fencing, a
crafted CV could push the model to emit an out-of-range or non-numeric score.
Clamping server-side means a poisoned CV can never store a >100 (or negative)
match score that would float the candidate to the top of a recruiter's board.
"""

import pytest

from app.modules.ai.application.analysis_service import _clamp_score


@pytest.mark.parametrize(
    "raw, expected",
    [
        (50, 50.0),
        (0, 0.0),
        (100, 100.0),
        (150, 100.0),  # injected inflation clamped down
        (999, 100.0),
        (-10, 0.0),  # negative clamped up
        (85.5, 85.5),
        ("85", 85.0),  # string number coerced
        ("not-a-number", 0.0),  # garbage → 0
        (None, 0.0),
        (float("nan"), 0.0),
    ],
)
def test_clamp_score_bounds_and_coerces(raw: object, expected: float) -> None:
    assert _clamp_score(raw) == expected
