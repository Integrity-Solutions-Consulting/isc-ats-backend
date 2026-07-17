"""Slot generation service for interview scheduling.

Converts interviewer availability windows into concrete UTC datetime slots
for a given target date, filtering out already-booked intervals.

R1 (timezone): availability windows (`start_time`/`end_time`) and `target_date`
are always expressed in Ecuador local time (UTC-5, no DST). Every instant is
converted local -> UTC exactly once, at the point a concrete slot datetime is
emitted. Booked intervals arrive as real UTC instants (as stored in the DB)
and are normalized back to Ecuador local time before being compared against
the local availability window.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone

# Ecuador does not observe DST — a fixed UTC-5 offset is correct year-round.
EC_TZ = timezone(timedelta(hours=-5))


@dataclass(frozen=True)
class AvailabilityWindow:
    """A single availability row projected for the service layer.

    `is_active` mirrors the soft-delete field from the ORM model.
    `day_of_week` follows Python's `datetime.weekday()` convention (0=Monday..6=Sunday).
    """

    user_id: int
    day_of_week: int
    start_time: time
    end_time: time
    slot_duration_min: int
    buffer_min: int
    is_active: bool


def generate_slots_for_window(
    *,
    window_start: time,
    window_end: time,
    slot_duration_min: int,
    buffer_min: int,
    booked_intervals: list[tuple[time, time]],
) -> list[time]:
    """Generate all free slot start-times within a single availability window.

    Rules:
    - Slots advance by `slot_duration_min + buffer_min` from `window_start`.
    - A slot is included only if `slot_start + slot_duration_min <= window_end`.
    - A slot is excluded if it overlaps any interval in `booked_intervals`.
      Overlap check: max(slot_start, booked_start) < min(slot_end, booked_end).

    All times are naive (caller handles timezone projection).
    """
    # Work in minutes from midnight to avoid time arithmetic edge-cases
    def to_min(t: time) -> int:
        return t.hour * 60 + t.minute

    def from_min(m: int) -> time:
        return time(m // 60, m % 60)

    start_min = to_min(window_start)
    end_min = to_min(window_end)
    step_min = slot_duration_min + buffer_min
    dur_min = slot_duration_min

    result: list[time] = []
    cursor = start_min
    while cursor + dur_min <= end_min:
        slot_start = cursor
        slot_end = cursor + dur_min

        # Check overlap against each booked interval
        overlaps = False
        for booked_s, booked_e in booked_intervals:
            b_start = to_min(booked_s)
            b_end = to_min(booked_e)
            if max(slot_start, b_start) < min(slot_end, b_end):
                overlaps = True
                break

        if not overlaps:
            result.append(from_min(slot_start))

        cursor += step_min

    return result


class SlotGenerationService:
    """Computes available interview slots for a target date.

    Designed as a pure in-memory service: callers pass the availability windows
    and booked interview intervals; no DB access happens here.
    """

    def get_available_slots(
        self,
        *,
        target_date: date,
        windows: list[AvailabilityWindow],
        booked_interviews: list[tuple[datetime, datetime]],
        now: datetime | None = None,
    ) -> list[datetime]:
        """Return sorted UTC datetimes for every free slot on `target_date`.

        Args:
            target_date: The calendar date to generate slots for.
            windows: Active availability rows for the interviewer (any day_of_week).
            booked_interviews: List of (start, end) datetimes for existing active
                non-cancelled interviews on the same date. Soft-deleted and cancelled
                interviews must NOT appear in this list — the caller is responsible
                for filtering them.
            now: Current instant (UTC). When provided AND `target_date` is
                `now`'s Ecuador calendar day, slots whose start has already
                passed are excluded (R7 — HR can't offer a time that's gone).
                Left `None` (default) to keep this service pure/deterministic;
                the real wall-clock read belongs to the caller.

        Returns:
            Sorted list of UTC datetime objects for free slot starts.
        """
        target_weekday = target_date.weekday()  # 0=Monday..6=Sunday

        # Convert booked intervals to time tuples on target_date, in Ecuador
        # LOCAL time — the availability window itself (start_time/end_time) is
        # local, so overlap comparisons must happen in the same space (D1a).
        booked_times: list[tuple[time, time]] = []
        for booked_start, booked_end in booked_interviews:
            # Normalize to Ecuador local time
            if booked_start.tzinfo is not None:
                bs = booked_start.astimezone(EC_TZ)
                be = booked_end.astimezone(EC_TZ)
            else:
                bs = booked_start.replace(tzinfo=EC_TZ)
                be = booked_end.replace(tzinfo=EC_TZ)

            bs_date = bs.date()
            be_date = be.date()
            if bs_date <= target_date <= be_date:
                start_time = bs.time() if bs_date == target_date else time(0, 0)
                end_time = be.time() if be_date == target_date else time(23, 59, 59)
                if start_time < end_time:
                    booked_times.append((start_time, end_time))

        result: list[datetime] = []
        for window in windows:
            if not window.is_active:
                continue
            if window.day_of_week != target_weekday:
                continue

            free_times = generate_slots_for_window(
                window_start=window.start_time,
                window_end=window.end_time,
                slot_duration_min=window.slot_duration_min,
                buffer_min=window.buffer_min,
                booked_intervals=booked_times,
            )
            for t in free_times:
                # The slot start is a wall-clock time on target_date, in Ecuador
                # local time. Convert to UTC exactly once, here — this also
                # correctly rolls the date over when a late local slot crosses
                # midnight UTC (e.g. 21:00 local -> 02:00 UTC next day).
                slot_local = datetime(
                    target_date.year,
                    target_date.month,
                    target_date.day,
                    t.hour,
                    t.minute,
                    tzinfo=EC_TZ,
                )
                slot_dt = slot_local.astimezone(UTC)
                result.append(slot_dt)

        result.sort()

        if now is not None and now.astimezone(EC_TZ).date() == target_date:
            result = [s for s in result if s > now]

        return result
