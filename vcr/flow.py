"""Caller-declared flow groups for cassette playback.

Two flows (e.g. two form-filling threads) can share a cassette and make
identical requests whose recorded responses differ. A flow group keeps each
flow's playback inside its own slice of the cassette: requests made while a
group is active only consume interactions recorded for that group, in
recording order, and never fall back to another group's interactions once the
group's own are exhausted.

The active group is kept in a :class:`contextvars.ContextVar`, so threads and
asyncio tasks get an isolated value automatically without the caller passing
the group through every HTTP call.
"""

import contextlib
import contextvars

# The flow group of the code making the current HTTP request, or None when no
# group is declared. Stored per context so concurrent threads/tasks don't share
# or overwrite each other's group.
_current_flow_group: "contextvars.ContextVar[str | None]" = contextvars.ContextVar(
    "vcr_flow_group",
    default=None,
)


def current_flow_group():
    """Return the flow group active in this context, or ``None``."""
    return _current_flow_group.get()


@contextlib.contextmanager
def flow_group(group):
    """Make ``group`` the active flow group for the duration of the context.

    Nested groups override the outer one; the outer group is restored on exit.
    A falsy ``group`` is treated as no group, which is the ungrouped default.
    """
    token = _current_flow_group.set(group or None)
    try:
        yield
    finally:
        _current_flow_group.reset(token)
