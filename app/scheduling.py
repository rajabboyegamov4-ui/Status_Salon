"""Pure time-interval logic for smart scheduling (no database access)."""

from collections.abc import Iterable
from datetime import datetime, timedelta

Interval = tuple[datetime, datetime]


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def subtract(intervals: Iterable[Interval], busy: Iterable[Interval]) -> list[Interval]:
    """Parts of `intervals` not covered by any of `busy`."""
    busy = merge(busy)
    result: list[Interval] = []
    for start, end in merge(intervals):
        cursor = start
        for b_start, b_end in busy:
            if b_end <= cursor or b_start >= end:
                continue
            if b_start > cursor:
                result.append((cursor, b_start))
            cursor = max(cursor, b_end)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return result


def align_up(t: datetime, step_min: int) -> datetime:
    """Round up to the next wall-clock multiple of `step_min` (10:07 -> 10:15)."""
    base = t.replace(second=0, microsecond=0)
    if base < t:
        base += timedelta(minutes=1)
    minute_of_day = base.hour * 60 + base.minute
    return base + timedelta(minutes=(-minute_of_day) % step_min)


def candidate_starts(
    free: Iterable[Interval], duration: timedelta, step_min: int, not_before: datetime
) -> list[datetime]:
    """Start times where `duration` fits entirely inside one free interval.

    Steps are aligned to the wall clock of `not_before`'s timezone (the branch's).
    """
    step = timedelta(minutes=step_min)
    tz = not_before.tzinfo
    starts: list[datetime] = []
    for start, end in free:
        t = align_up(max(start, not_before).astimezone(tz), step_min)
        while t + duration <= end:
            starts.append(t)
            t += step
    return starts
