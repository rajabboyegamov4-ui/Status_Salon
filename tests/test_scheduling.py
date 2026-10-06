from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.scheduling import align_up, candidate_starts, merge, subtract

TZ = ZoneInfo("Asia/Tashkent")


def t(hh, mm=0):
    return datetime(2026, 10, 6, hh, mm, tzinfo=TZ)


def test_merge_joins_overlapping_and_touching():
    assert merge([(t(12), t(13)), (t(10), t(11)), (t(11), t(11, 30))]) == [(t(10), t(11, 30)), (t(12), t(13))]


def test_subtract_cuts_busy_out():
    assert subtract([(t(10), t(14))], [(t(11), t(12)), (t(13, 30), t(15))]) == [
        (t(10), t(11)),
        (t(12), t(13, 30)),
    ]


def test_align_up():
    assert align_up(t(10, 7), 15) == t(10, 15)
    assert align_up(t(10, 15), 15) == t(10, 15)
    assert align_up(t(10, 15) + timedelta(seconds=1), 15) == t(10, 30)


def test_slots_only_where_whole_service_fits():
    free = subtract([(t(10), t(13))], [(t(11), t(11, 50))])
    starts = candidate_starts(free, timedelta(minutes=30), 15, not_before=t(9))
    assert starts == [t(10), t(10, 15), t(10, 30), t(12), t(12, 15), t(12, 30)]


def test_not_before_is_respected():
    starts = candidate_starts([(t(10), t(12))], timedelta(minutes=50), 15, not_before=t(10, 20))
    assert starts == [t(10, 30), t(10, 45), t(11)]
