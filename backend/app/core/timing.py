"""Latency tracing for the inbound email path.

One email's journey crosses the poller, the provider, the intake service and
SMTP, so "why did that reply take 4 seconds?" cannot be answered from any one
of them. This records monotonic marks along the way and lets the poller emit a
single line with the breakdown.

Deliberately tiny: a dict of marks on a context variable. No sampling, no
exporter, no dependency. ``asyncio.to_thread`` copies the context, so a mark
made inside a worker thread lands on the same trace.

Nothing here ever records message content, addresses or credentials - only
names and elapsed milliseconds.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

# The stages, in the order they occur. T0/T1 are set by the IDLE connection
# and handed to the trace by the poller; the rest are marked in place.
NOTIFIED = "notified"            # T0 - IDLE reported activity
IDLE_EXITED = "idle_exited"      # T1 - DONE acknowledged, connection usable again
POLL_STARTED = "poll_started"    # T2
FETCHED = "fetched"              # T3 - message parsed out of the mailbox
INTAKE_STARTED = "intake_started"  # T4
INTAKE_FINISHED = "intake_finished"  # T5
SMTP_STARTED = "smtp_started"    # T6
SMTP_FINISHED = "smtp_finished"  # T7


@dataclass
class LatencyTrace:
    """Monotonic marks for one inbound email."""

    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str, value: float | None = None) -> None:
        # First write wins: a retry inside one turn must not move T0.
        self.marks.setdefault(name, value if value is not None else time.monotonic())

    def span_ms(self, start: str, end: str) -> int | None:
        a, b = self.marks.get(start), self.marks.get(end)
        # round, not int: float subtraction gives 0.03999... for 40ms.
        return None if a is None or b is None else round((b - a) * 1000)

    def summary(self) -> str:
        """A compact, log-safe breakdown. Missing stages are simply absent.

        ``handler`` spans the whole intake turn, which includes the SMTP send
        and the commit - the reply is sent inside the transaction by design -
        so ``processing_to_smtp`` is the figure for engine + database work
        alone.
        """
        parts: list[str] = []
        for label, a, b in (
            ("detect_to_fetch", NOTIFIED, FETCHED),       # T0 -> T3
            ("idle_exit", NOTIFIED, IDLE_EXITED),         # T0 -> T1
            ("fetch_to_processing", FETCHED, INTAKE_STARTED),  # T3 -> T4
            ("processing_to_smtp", INTAKE_STARTED, SMTP_STARTED),  # T4 -> T6
            ("smtp", SMTP_STARTED, SMTP_FINISHED),        # T6 -> T7
            ("handler", INTAKE_STARTED, INTAKE_FINISHED),  # T4 -> T5
        ):
            ms = self.span_ms(a, b)
            if ms is not None:
                parts.append(f"{label}={ms}ms")
        # From the notification when there was one; otherwise from the start
        # of the cycle (the startup drain, or a timeout-driven poll).
        total = self.span_ms(NOTIFIED, SMTP_FINISHED)
        if total is None:
            total = self.span_ms(POLL_STARTED, INTAKE_FINISHED)
        if total is not None:
            parts.append(f"total={total}ms")
        return " ".join(parts)


_current: ContextVar[LatencyTrace | None] = ContextVar("latency_trace", default=None)


def start_trace(**seed: float) -> LatencyTrace:
    """Begin a trace for this task, optionally seeded with earlier marks."""
    trace = LatencyTrace()
    for name, value in seed.items():
        if value is not None:
            trace.mark(name, value)
    _current.set(trace)
    return trace


def current_trace() -> LatencyTrace | None:
    return _current.get()


# Marks that belong to the poll cycle rather than to any one message.
_CYCLE_MARKS = (NOTIFIED, IDLE_EXITED, POLL_STARTED, FETCHED)


@contextmanager
def message_trace() -> Iterator[LatencyTrace | None]:
    """A fresh trace for one message, inheriting the cycle's detection marks.

    One poll can fetch several messages. Without this they would share a
    trace, and because the first write to a mark wins, the second message
    would report the first one's processing and SMTP times as its own.
    """
    cycle = _current.get()
    if cycle is None:
        yield None
        return
    child = LatencyTrace(
        marks={k: v for k, v in cycle.marks.items() if k in _CYCLE_MARKS}
    )
    token = _current.set(child)
    try:
        yield child
    finally:
        _current.reset(token)


def mark(name: str, value: float | None = None) -> None:
    """Record a stage on the active trace, if there is one.

    Safe to call from anywhere: with no trace running this is a no-op, so the
    instrumentation never changes behaviour outside the poller's path.
    """
    trace = _current.get()
    if trace is not None:
        trace.mark(name, value)


def clear_trace() -> None:
    _current.set(None)
