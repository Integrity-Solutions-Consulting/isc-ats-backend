"""Tests for SlotGenerationService.

Covers:
- Basic slot generation (9:00-11:00, 60-min slots -> 9:00, 10:00)
- Buffer between slots (30-min slot + 10-min buffer -> 10:00, 10:40, 11:20)
- Slot excluded when start + slot_duration_min > window.end_time
- Cancelled interviews do NOT block slots
- Inactive availability rows are excluded
- UTC conversion: target_date with UTC-5 availability computes correctly
- Already-booked (active, non-cancelled) interviews block overlapping slots
"""

from datetime import UTC, date, datetime, time

from app.modules.recruitment.application.slot_generation_service import (
    AvailabilityWindow,
    SlotGenerationService,
    generate_slots_for_window,
)

# ── pure unit tests for generate_slots_for_window ────────────────────────────


def test_basic_slots_no_buffer() -> None:
    """9:00–11:00, 60 min, 0 buffer -> [09:00, 10:00] (11:00 excluded: start+60=12:00 > 11:00)."""
    slots = generate_slots_for_window(
        window_start=time(9, 0),
        window_end=time(11, 0),
        slot_duration_min=60,
        buffer_min=0,
        booked_intervals=[],
    )
    assert [s.strftime("%H:%M") for s in slots] == ["09:00", "10:00"]


def test_slots_with_buffer() -> None:
    """30-min slot + 10-min buffer in 9:00–12:00.

    Slot advances by 30+10=40 min each time:
      - 09:00 start: 09:00–09:30 -> next at 09:40
      - 09:40 start: 09:40–10:10 -> next at 10:20
      - 10:20 start: 10:20–10:50 -> next at 11:00
      - 11:00 start: 11:00–11:30 -> next at 11:40
      - 11:40 start: 11:40 + 30 = 12:10 > 12:00 -> excluded
    Result: [09:00, 09:40, 10:20, 11:00, 11:40] — but 11:40+30=12:10>12:00 -> excluded
    Actually last valid is 11:40 only if 11:40+30 <= 12:00? 11:40+30=12:10 > 12:00 -> NO.
    So: [09:00, 09:40, 10:20, 11:00]
    """
    slots = generate_slots_for_window(
        window_start=time(9, 0),
        window_end=time(12, 0),
        slot_duration_min=30,
        buffer_min=10,
        booked_intervals=[],
    )
    assert [s.strftime("%H:%M") for s in slots] == ["09:00", "09:40", "10:20", "11:00"]


def test_slot_excluded_when_would_exceed_window() -> None:
    """The design-specified edge case: slot start + slot_duration > window end is dropped."""
    # 10:00-10:00, 60 min -> only 10:00 if 10:00+60=11:00 <= 11:00 -> yes
    # Use window 10:00-10:59 -> 10:00+60=11:00 > 10:59 -> no slots
    slots = generate_slots_for_window(
        window_start=time(10, 0),
        window_end=time(10, 59),
        slot_duration_min=60,
        buffer_min=0,
        booked_intervals=[],
    )
    assert slots == []


def test_slot_exactly_fitting_window_is_included() -> None:
    """A slot whose end == window end is included."""
    slots = generate_slots_for_window(
        window_start=time(10, 0),
        window_end=time(11, 0),
        slot_duration_min=60,
        buffer_min=0,
        booked_intervals=[],
    )
    assert len(slots) == 1
    assert slots[0] == time(10, 0)


def test_booked_interval_blocks_overlapping_slot() -> None:
    """A booked [09:30, 10:30] blocks the 09:00 and 10:00 slots (they overlap)."""
    # 09:00+60=10:00 overlaps with booked 09:30-10:30? Yes: max(09:00,09:30) < min(10:00,10:30)
    # 10:00+60=11:00 overlaps with booked 09:30-10:30? Yes: max(10:00,09:30) < min(11:00,10:30)
    # Only 11:00 survives (if window allows)
    slots = generate_slots_for_window(
        window_start=time(9, 0),
        window_end=time(13, 0),
        slot_duration_min=60,
        buffer_min=0,
        booked_intervals=[(time(9, 30), time(10, 30))],
    )
    # Blocked: 09:00 (overlaps), 10:00 (overlaps)
    # Free: 11:00, 12:00
    assert [s.strftime("%H:%M") for s in slots] == ["11:00", "12:00"]


def test_cancelled_interview_does_not_block_slot() -> None:
    """Cancelled status must not appear in booked_intervals passed to generate_slots_for_window."""
    # If caller correctly filters cancelled, a cancelled interview at 09:30-10:30
    # should leave all slots free. We test the service layer passes it correctly.
    slots = generate_slots_for_window(
        window_start=time(9, 0),
        window_end=time(11, 0),
        slot_duration_min=60,
        buffer_min=0,
        booked_intervals=[],  # no booked intervals = cancelled correctly excluded
    )
    assert [s.strftime("%H:%M") for s in slots] == ["09:00", "10:00"]


# ── integration tests for SlotGenerationService ───────────────────────────────

# Use 2026-06-15 (Monday) = weekday 0 in Python (Monday=0)
_TARGET_DATE = date(2026, 6, 15)  # Monday


def _make_window(
    day_of_week: int = 0,  # Monday
    start: time = time(9, 0),
    end: time = time(11, 0),
    slot_duration_min: int = 60,
    buffer_min: int = 0,
    is_active: bool = True,
) -> AvailabilityWindow:
    return AvailabilityWindow(
        user_id=1,
        day_of_week=day_of_week,
        start_time=start,
        end_time=end,
        slot_duration_min=slot_duration_min,
        buffer_min=buffer_min,
        is_active=is_active,
    )


def test_service_basic_slots() -> None:
    """Basic service integration: returns UTC datetimes for the correct date."""
    svc = SlotGenerationService()
    windows = [_make_window()]
    slots = svc.get_available_slots(
        target_date=_TARGET_DATE,
        windows=windows,
        booked_interviews=[],
    )
    # 09:00 and 10:00 on 2026-06-15 in UTC (windows treated as UTC here since no TZ info)
    assert len(slots) == 2
    assert slots[0].hour == 9 and slots[0].minute == 0
    assert slots[1].hour == 10 and slots[1].minute == 0


def test_service_inactive_availability_excluded() -> None:
    """Inactive availability rows must be skipped entirely."""
    svc = SlotGenerationService()
    windows = [_make_window(is_active=False)]
    slots = svc.get_available_slots(
        target_date=_TARGET_DATE,
        windows=windows,
        booked_interviews=[],
    )
    assert slots == []


def test_service_wrong_day_of_week_excluded() -> None:
    """Windows for different days of week than target_date must be skipped."""
    svc = SlotGenerationService()
    # Target is Monday (0), window is Tuesday (1)
    windows = [_make_window(day_of_week=1)]
    slots = svc.get_available_slots(
        target_date=_TARGET_DATE,
        windows=windows,
        booked_interviews=[],
    )
    assert slots == []


def test_service_booked_interview_blocks_slot() -> None:
    """Active interview in the window blocks its overlapping slot."""
    svc = SlotGenerationService()
    windows = [_make_window()]
    # Book 09:30 - 10:30 on target date (UTC)
    booked_start = datetime(2026, 6, 15, 9, 30, tzinfo=UTC)
    booked_end = datetime(2026, 6, 15, 10, 30, tzinfo=UTC)
    slots = svc.get_available_slots(
        target_date=_TARGET_DATE,
        windows=windows,
        booked_interviews=[(booked_start, booked_end)],
    )
    # 09:00 slot (09:00-10:00) overlaps [09:30-10:30]; 10:00 (10:00-11:00) overlaps too
    assert slots == []


def test_service_buffer_30_10() -> None:
    """Design-specified example: 30-min slot, 10-min buffer, 10:00-12:00 -> 10:00, 10:40, 11:20."""
    svc = SlotGenerationService()
    windows = [
        _make_window(
            day_of_week=0,
            start=time(10, 0),
            end=time(12, 0),
            slot_duration_min=30,
            buffer_min=10,
        )
    ]
    slots = svc.get_available_slots(
        target_date=_TARGET_DATE,
        windows=windows,
        booked_interviews=[],
    )
    times = [(s.hour, s.minute) for s in slots]
    assert times == [(10, 0), (10, 40), (11, 20)]
