"""Tests for the ingestion schedule and wait-loop.

These tests pin down WHEN the nightly ingestion is allowed to run and ensure
the wait loop actually wakes up inside the window — the latter is a regression
test for a bug where the loop slept through every nightly run because it
checked ``seconds_until_window > 0`` (always true) instead of ``in_window``.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from ingestion.run import (
    DEFAULT_DURATION_MINUTES,
    DEFAULT_LIMIT_PER_SOURCE,
    DEFAULT_START_HOUR,
    DEFAULT_START_MINUTE,
    Schedule,
    _wait_for_window,
    build_schedule,
    write_schedule_file,
)

TZ = ZoneInfo("Europe/Berlin")


def _t(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 4, 10, hour, minute, second, tzinfo=TZ)


class TestDefaultNightlySchedule:
    """The production schedule: 00:00–06:00 Europe/Berlin."""

    def setup_method(self):
        self.s = Schedule(
            start_hour=DEFAULT_START_HOUR,
            start_minute=DEFAULT_START_MINUTE,
            duration_minutes=DEFAULT_DURATION_MINUTES,
            tz=TZ,
        )

    def test_in_window_at_start(self):
        assert self.s.in_window(_t(0, 0))

    def test_in_window_middle(self):
        assert self.s.in_window(_t(3, 0))

    def test_in_window_just_before_end(self):
        assert self.s.in_window(_t(5, 59, 59))

    def test_out_of_window_at_end(self):
        assert not self.s.in_window(_t(6, 0))

    def test_out_of_window_during_day(self):
        assert not self.s.in_window(_t(12, 0))

    def test_out_of_window_late_evening(self):
        assert not self.s.in_window(_t(23, 30))

    def test_seconds_until_window_inside_is_zero(self):
        assert self.s.seconds_until_window(_t(3, 0)) == 0.0

    def test_seconds_until_window_after_window(self):
        # 06:00 — next window opens at 00:00 tomorrow → 18 hours
        assert self.s.seconds_until_window(_t(6, 0)) == 18 * 3600

    def test_seconds_until_window_just_before_window(self):
        # 23:59 — window opens in 1 minute
        assert self.s.seconds_until_window(_t(23, 59)) == 60

    def test_seconds_until_window_at_exact_start_is_zero(self):
        # Regression: at exactly 00:00 we are inside the window, not waiting
        # 24h for "tomorrow's" window. The original code returned 86400 here.
        assert self.s.seconds_until_window(_t(0, 0)) == 0.0


class TestArbitraryShortSchedule:
    """A short configurable window — used for testing the schedule machinery
    without waiting for the production 00:00–06:00 slot."""

    def test_in_window_at_start(self):
        s = Schedule(start_hour=14, start_minute=23, duration_minutes=30, tz=TZ)
        assert s.in_window(_t(14, 23))

    def test_in_window_29_minutes_in(self):
        s = Schedule(start_hour=14, start_minute=23, duration_minutes=30, tz=TZ)
        assert s.in_window(_t(14, 52))

    def test_out_of_window_after_30_minutes(self):
        s = Schedule(start_hour=14, start_minute=23, duration_minutes=30, tz=TZ)
        assert not s.in_window(_t(14, 53))

    def test_out_of_window_one_hour_later(self):
        s = Schedule(start_hour=14, start_minute=23, duration_minutes=30, tz=TZ)
        assert not s.in_window(_t(15, 23))

    def test_window_reopens_next_day(self):
        s = Schedule(start_hour=14, start_minute=23, duration_minutes=30, tz=TZ)
        tomorrow = _t(14, 23) + timedelta(days=1)
        assert s.in_window(tomorrow)


class TestWindowCrossingMidnight:
    """A window can start late evening and continue past midnight."""

    def test_in_window_before_midnight(self):
        s = Schedule(start_hour=23, start_minute=30, duration_minutes=120, tz=TZ)
        assert s.in_window(_t(23, 45))

    def test_in_window_after_midnight(self):
        s = Schedule(start_hour=23, start_minute=30, duration_minutes=120, tz=TZ)
        assert s.in_window(_t(0, 30))

    def test_out_of_window_after_end(self):
        s = Schedule(start_hour=23, start_minute=30, duration_minutes=120, tz=TZ)
        assert not s.in_window(_t(2, 0))


class TestBuildSchedule:
    def test_default_args_produce_nightly_schedule(self):
        args = SimpleNamespace(
            start_hour=DEFAULT_START_HOUR,
            start_minute=DEFAULT_START_MINUTE,
            duration=DEFAULT_DURATION_MINUTES,
            tz="Europe/Berlin",
        )
        s = build_schedule(args)
        assert s.start_hour == 0
        assert s.start_minute == 0
        assert s.duration_minutes == 360
        assert s.tz.key == "Europe/Berlin"

    def test_custom_args_produce_custom_schedule(self):
        args = SimpleNamespace(
            start_hour=14,
            start_minute=23,
            duration=30,
            tz="Europe/Berlin",
        )
        s = build_schedule(args)
        assert s.start_hour == 14
        assert s.start_minute == 23
        assert s.duration_minutes == 30

    def test_default_limit_is_safe(self):
        # Sanity check: production default for limit
        assert DEFAULT_LIMIT_PER_SOURCE == 100


class TestScheduleSerialization:
    def test_to_dict_contains_window_bounds(self):
        s = Schedule(start_hour=22, start_minute=30, duration_minutes=120, tz=TZ)
        d = s.to_dict()
        assert d["start_hour"] == 22
        assert d["start_minute"] == 30
        assert d["duration_minutes"] == 120
        assert d["end_hour"] == 0
        assert d["end_minute"] == 30
        assert d["tz"] == "Europe/Berlin"

    def test_describe_includes_start_end_and_duration(self):
        s = Schedule(start_hour=0, start_minute=0, duration_minutes=360, tz=TZ)
        text = s.describe()
        assert "00:00" in text
        assert "06:00" in text
        assert "Europe/Berlin" in text
        assert "360 min" in text

    def test_write_schedule_file(self, tmp_path):
        s = Schedule(start_hour=2, start_minute=15, duration_minutes=45, tz=TZ)
        sources = ["wiki", "arxiv"]
        path = write_schedule_file(tmp_path, s, limit=7, sources=sources)
        assert path.exists()
        import json
        data = json.loads(path.read_text())
        assert data["start_hour"] == 2
        assert data["start_minute"] == 15
        assert data["duration_minutes"] == 45
        assert data["end_hour"] == 3
        assert data["end_minute"] == 0
        assert data["tz"] == "Europe/Berlin"
        assert data["limit_per_source"] == 7
        assert data["sources"] == sources
        assert "started_at" in data


class TestWaitForWindow:
    def test_returns_immediately_when_in_window(self):
        s = Schedule(start_hour=0, start_minute=0, duration_minutes=1440, tz=TZ)
        sleeps: list[float] = []
        _wait_for_window(s, sleep_fn=sleeps.append, is_shutdown=lambda: False)
        assert sleeps == []

    def test_exits_when_window_opens_after_sleep(self):
        """Regression test for the nightly-ingestion bug.

        Previously the wait loop only checked ``seconds_until_window > 0``
        (always true) and would loop forever — the process slept through
        every nightly window. The loop must instead re-check ``in_window``
        and exit once the window has opened.
        """
        s = Schedule(start_hour=0, start_minute=0, duration_minutes=360, tz=TZ)
        # Outside the window for the first two checks (initial check + start
        # of the loop), then inside. Decoupled from seconds_until_window so
        # the test only counts explicit in_window calls.
        states = [False, False, True]
        s.in_window = lambda now=None: states.pop(0) if states else True  # type: ignore[method-assign]
        s.seconds_until_window = lambda now=None: 30.0  # type: ignore[method-assign]

        sleeps: list[float] = []
        _wait_for_window(s, sleep_fn=sleeps.append, is_shutdown=lambda: False)

        # We must have slept at least once (because in_window was False
        # initially) and then exited once it became True.
        assert len(sleeps) == 1
        assert sleeps[0] == 30.0
        assert states == []  # all states consumed

    def test_exits_when_shutdown_requested(self):
        s = Schedule(start_hour=0, start_minute=0, duration_minutes=1, tz=TZ)
        s.in_window = lambda now=None: False  # type: ignore[method-assign]
        shutdown_flag = {"v": False}
        sleeps: list[float] = []

        def sleep_then_shutdown(secs):
            sleeps.append(secs)
            shutdown_flag["v"] = True

        _wait_for_window(
            s,
            sleep_fn=sleep_then_shutdown,
            is_shutdown=lambda: shutdown_flag["v"],
        )
        assert len(sleeps) == 1

    def test_sleeps_in_short_intervals_for_signal_responsiveness(self):
        """The wait loop must not sleep for hours at a time, so SIGTERM is honored."""
        s = Schedule(start_hour=0, start_minute=0, duration_minutes=1, tz=TZ)
        # Pretend we are 12 hours away from the window
        s.seconds_until_window = lambda now=None: 12 * 3600  # type: ignore[method-assign]
        states = [False, False, True]
        s.in_window = lambda now=None: states.pop(0) if states else True  # type: ignore[method-assign]

        sleeps: list[float] = []
        _wait_for_window(s, sleep_fn=sleeps.append, is_shutdown=lambda: False)

        assert sleeps, "should have slept at least once"
        assert max(sleeps) <= 60.0, f"sleep chunks must be <=60s, got {sleeps}"
