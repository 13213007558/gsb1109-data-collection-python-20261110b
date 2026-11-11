"""Per-task cassette lookup for concurrent, overlapping cassettes.

The patchers VCR.py installs are process-wide: while a cassette is in use it
replaces, for example, ``aiohttp.ClientSession._request`` on the class. Two
cassettes active at the same time in different ``asyncio`` tasks (or threads)
therefore share one patched attribute, and whichever cassette entered last
also answers requests made by the other task.

``contextvars`` values are copied into each new task, so binding the active
cassette to a context variable lets a patched request find the cassette of
the task that makes it instead of the one that patched the class last. This
is opt-in (``use_contextvar=True``): the default keeps the process-wide
behavior, including nested cassettes entered synchronously.
"""

import contextvars

# The cassette governing the current task, or None when no context-aware
# cassette is in scope. A new task copies the value of the task that creates
# it, and each task's later changes stay in its own context, so canceling one
# task never changes what another task sees.
current_cassette = contextvars.ContextVar("vcr_current_cassette", default=None)
