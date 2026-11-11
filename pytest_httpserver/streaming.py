from __future__ import annotations

import select
import socket
import threading
import time
from http import HTTPStatus
from typing import TYPE_CHECKING

from werkzeug import Response

if TYPE_CHECKING:
    from typing import Self

    from collections.abc import Generator
    from collections.abc import Iterable

    from _typeshed.wsgi import StartResponse
    from _typeshed.wsgi import WSGIEnvironment

    from .httpserver import HEADERS_T

#: Maximum time spent in a single select() call while waiting for a segment
#: to be released. It only bounds how often the client connection is checked
#: for a disconnect; the effective timeout is driven by the ``timeout``
#: parameter of the :py:class:`SegmentedResponse`.
_POLL_INTERVAL = 0.05


class SegmentedResponse(Response):
    """
    A response whose body is delivered segment by segment with explicit,
    event-driven flow control.

    The first segment is sent as soon as the request is handled; every further
    segment is held back until :py:meth:`release` or :py:meth:`release_all` is
    called from the test thread. Tests synchronize on :py:meth:`wait_until_sent`
    instead of sleeping for a fixed amount of time, so they do not depend on
    scheduling timing.

    A client disconnect (or :py:meth:`abort`) unblocks every segment that is
    still held, which guarantees that the handler thread and the test fixtures
    tearing the server down cannot get stuck waiting for a release that never
    arrives.

    Werkzeug serves the iterable body without a ``Content-Length`` header, so
    the body is transferred with chunked transfer encoding.

    A :py:class:`SegmentedResponse` serves a single request. Create a new
    instance for every request, for example with a oneshot handler.

    :param segments: body segments in sending order; strings are encoded as
        UTF-8, bytes are sent as-is.
    :param status: the HTTP status of the response.
    :param headers: the HTTP headers to be sent (excluding the Content-Type
        header).
    :param content_type: the content type header to be sent, for example
        ``"text/csv"``.
    :param timeout: maximum time in seconds to wait for a segment to be
        released or for the client to disconnect. It is also used as the
        default timeout of the wait methods.
    """

    def __init__(
        self,
        segments: Iterable[str | bytes],
        status: int = HTTPStatus.OK.value,
        headers: HEADERS_T | None = None,
        content_type: str | None = None,
        timeout: float = 5,
    ) -> None:
        self._segments: tuple[bytes, ...] = tuple(
            segment.encode() if isinstance(segment, str) else segment for segment in segments
        )
        self._timeout = timeout
        self._release_events: list[threading.Event] = [threading.Event() for _ in self._segments]
        self._sent_events: list[threading.Event] = [threading.Event() for _ in self._segments]
        if self._release_events:
            self._release_events[0].set()
        self._aborted = threading.Event()
        self._finished = threading.Event()
        self._used = False
        self._socket: socket.socket | None = None
        super().__init__(self._generate(), status=status, headers=headers, content_type=content_type)

    def __call__(self, environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        if self._used:
            raise RuntimeError("SegmentedResponse can only serve a single request")
        self._used = True
        self._socket = environ.get("werkzeug.socket")
        return super().__call__(environ, start_response)

    @property
    def aborted(self) -> bool:
        """Whether the response was aborted because the client disconnected."""
        return self._aborted.is_set()

    @property
    def completed(self) -> bool:
        """Whether the handler thread finished iterating the response body."""
        return self._finished.is_set()

    def _wait_release(self, index: int) -> None:
        event = self._release_events[index]
        if event.is_set() or self._aborted.is_set():
            return

        socket_ = self._socket
        if socket_ is None:
            # Running outside of a werkzeug server (eg. in a unit test): fall
            # back to a plain event wait while still honoring the timeout.
            event.wait(self._timeout)
            if not event.is_set() and not self._aborted.is_set():
                raise TimeoutError(f"Segment {index} was not released within {self._timeout} seconds")  # noqa: EM102
            return

        deadline = time.monotonic() + self._timeout
        while not event.is_set() and not self._aborted.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Segment {index} was not released within {self._timeout} seconds")  # noqa: EM102
            readable, _, _ = select.select([socket_], [], [], min(_POLL_INTERVAL, remaining))
            if not readable:
                continue
            try:
                data = socket_.recv(1, socket.MSG_PEEK)
            except OSError:
                self._aborted.set()
                return
            if data == b"":
                # The client closed the connection: stop producing body data
                # instead of blocking until a release that can never arrive.
                self._aborted.set()
                return

    def _generate(self) -> Generator[bytes, None, None]:
        try:
            for index, segment in enumerate(self._segments):
                self._wait_release(index)
                if self._aborted.is_set():
                    return
                yield segment
                self._sent_events[index].set()
        finally:
            self._finished.set()

    def wait_until_sent(self, segments: int = 1, timeout: float | None = None) -> Self:
        """
        Block until the given number of leading segments have been handed to
        the server for sending.

        :param segments: 1-based number of segments to wait for.
        :param timeout: maximum waiting time; defaults to the response timeout.
        """
        if not 1 <= segments <= len(self._sent_events):
            raise ValueError(f"segments must be between 1 and {len(self._sent_events)}")  # noqa: EM102
        wait_timeout = self._timeout if timeout is None else timeout
        if not self._sent_events[segments - 1].wait(wait_timeout):
            raise TimeoutError(f"Segment {segments} was not sent within {wait_timeout} seconds")  # noqa: EM102
        return self

    def release(self, segments: int = 1) -> Self:
        """
        Release the given number of segments that are still held back.

        Already released segments are skipped, so calling ``release(1)`` twice
        releases the next two segments. Calling it after every segment was
        released has no effect.

        :param segments: number of still-held segments to release.
        """
        released = 0
        for event in self._release_events:
            if released >= segments:
                break
            if not event.is_set():
                event.set()
                released += 1
        return self

    def release_all(self) -> Self:
        """Release every segment that is still held back."""
        for event in self._release_events:
            event.set()
        return self

    def abort(self) -> Self:
        """
        Stop producing body data and release every segment that is still held.

        This is invoked automatically when the client disconnects and by the
        server while it is being torn down, but it can also be called directly
        from a test.
        """
        self._aborted.set()
        for event in self._release_events:
            event.set()
        return self

    def wait_for_completion(self, timeout: float | None = None) -> Self:
        """
        Block until the handler thread finished iterating the response body,
        either by delivering every segment or by stopping after a disconnect
        or an abort.

        :param timeout: maximum waiting time; defaults to the response timeout.
        """
        wait_timeout = self._timeout if timeout is None else timeout
        if not self._finished.wait(wait_timeout):
            raise TimeoutError(f"Response did not finish within {wait_timeout} seconds")  # noqa: EM102
        return self
