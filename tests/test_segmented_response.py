import socket
import threading
from collections.abc import Iterable
from typing import TYPE_CHECKING
from typing import cast

import pytest

from pytest_httpserver import HTTPServer
from pytest_httpserver import SegmentedResponse

if TYPE_CHECKING:
    from _typeshed.wsgi import StartResponse


def noop_start_response(status, headers, exc_info=None):  # noqa: ARG001
    return lambda _data: None


class ResponseReader:
    """Drives a SegmentedResponse the same way werkzeug would, in a thread."""

    def __init__(self, response: SegmentedResponse, peer_socket: socket.socket) -> None:
        environ = {"werkzeug.socket": peer_socket, "REQUEST_METHOD": "GET"}
        self.status: str | None = None
        self.headers: list[tuple[str, str]] = []
        self.items: list[bytes] = []
        self.error: BaseException | None = None

        def start_response(status: str, headers: list[tuple[str, str]], _exc_info: object = None) -> object:
            self.status = status
            self.headers = headers
            return lambda data: None

        self.app_iter: Iterable[bytes] = response(
            environ, cast("StartResponse", start_response)
        )
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float = 5) -> None:
        self._thread.join(timeout)
        assert not self._thread.is_alive()

    def _run(self) -> None:
        try:
            for item in self.app_iter:
                self.items.append(item)
        except BaseException as exc:  # noqa: BLE001 - re-raised via the error attribute
            self.error = exc
        finally:
            close = getattr(self.app_iter, "close", None)
            if close is not None:
                close()


@pytest.fixture
def socket_pair() -> Iterable[tuple[socket.socket, socket.socket]]:
    server_socket, client_socket = socket.socketpair()
    yield server_socket, client_socket
    server_socket.close()
    client_socket.close()


def test_first_segment_is_sent_without_release(socket_pair: tuple[socket.socket, socket.socket]):
    server_socket, _ = socket_pair
    response = SegmentedResponse([b"first", b"second", b"third"])
    reader = ResponseReader(response, server_socket)
    reader.start()

    response.wait_until_sent(1)
    assert reader.items == [b"first"]
    assert not response.completed

    response.release(1)
    response.wait_until_sent(2)
    assert reader.items == [b"first", b"second"]

    response.release_all()
    response.wait_for_completion()
    reader.join()
    assert reader.items == [b"first", b"second", b"third"]
    assert reader.error is None
    assert response.completed
    assert not response.aborted


def test_segment_is_held_until_released(socket_pair: tuple[socket.socket, socket.socket]):
    server_socket, _ = socket_pair
    response = SegmentedResponse([b"first", b"second"], timeout=5)
    reader = ResponseReader(response, server_socket)
    reader.start()

    response.wait_until_sent(1)
    with pytest.raises(TimeoutError, match="Segment 2"):
        response.wait_until_sent(2, timeout=0.05)
    assert reader.items == [b"first"]

    response.release(1)
    response.wait_until_sent(2)
    response.wait_for_completion()
    reader.join()
    assert reader.items == [b"first", b"second"]


def test_release_counts_only_still_held_segments(socket_pair: tuple[socket.socket, socket.socket]):
    server_socket, _ = socket_pair
    response = SegmentedResponse([b"a", b"b", b"c", b"d"])
    reader = ResponseReader(response, server_socket)
    reader.start()

    response.wait_until_sent(1)
    # Releasing more segments than remain, or releasing twice, must not raise.
    response.release(2)
    response.wait_until_sent(3)
    response.release(10)
    response.release_all()
    response.wait_for_completion()
    reader.join()
    assert reader.items == [b"a", b"b", b"c", b"d"]


def test_client_disconnect_unblocks_held_response(socket_pair: tuple[socket.socket, socket.socket]):
    server_socket, client_socket = socket_pair
    response = SegmentedResponse([b"first", b"second"], timeout=5)
    reader = ResponseReader(response, server_socket)
    reader.start()

    response.wait_until_sent(1)
    client_socket.close()

    response.wait_for_completion()
    reader.join()
    assert response.aborted
    assert reader.items == [b"first"]
    assert reader.error is None


def test_abort_releases_every_held_segment(socket_pair: tuple[socket.socket, socket.socket]):
    server_socket, _ = socket_pair
    response = SegmentedResponse([b"first", b"second", b"third"])
    reader = ResponseReader(response, server_socket)
    reader.start()

    response.wait_until_sent(1)
    response.abort()

    response.wait_for_completion()
    reader.join()
    assert response.aborted
    assert reader.items == [b"first"]
    assert reader.error is None


def test_timeout_raised_when_segment_never_released(socket_pair: tuple[socket.socket, socket.socket]):
    server_socket, _ = socket_pair
    response = SegmentedResponse([b"first", b"second"], timeout=0.05)
    reader = ResponseReader(response, server_socket)
    reader.start()

    response.wait_until_sent(1)
    reader.join()
    assert isinstance(reader.error, TimeoutError)
    assert response.completed


def test_string_segments_are_encoded_and_headers_preserved(socket_pair: tuple[socket.socket, socket.socket]):
    server_socket, _ = socket_pair
    response = SegmentedResponse(
        ["header\n", "row\n"],
        headers={"X-Test": "1"},
        content_type="text/csv",
    )
    reader = ResponseReader(response, server_socket)
    reader.start()

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/csv"
    response.release_all()
    response.wait_for_completion()
    reader.join()
    assert reader.status == "200 OK"
    assert ("X-Test", "1") in reader.headers
    assert reader.items == [b"header\n", b"row\n"]


def test_wait_until_sent_validates_segment_index(socket_pair: tuple[socket.socket, socket.socket]):
    server_socket, _ = socket_pair
    response = SegmentedResponse([b"only"])
    ResponseReader(response, server_socket).start()
    response.wait_until_sent(1)
    with pytest.raises(ValueError, match="between 1 and 1"):
        response.wait_until_sent(0)
    with pytest.raises(ValueError, match="between 1 and 1"):
        response.wait_until_sent(2)
    response.wait_for_completion()


def test_response_can_only_serve_one_request(socket_pair: tuple[socket.socket, socket.socket]):
    server_socket, _ = socket_pair
    response = SegmentedResponse([b"data"])
    reader = ResponseReader(response, server_socket)
    reader.start()
    response.wait_for_completion()
    reader.join()

    environ = {"werkzeug.socket": server_socket, "REQUEST_METHOD": "GET"}
    with pytest.raises(RuntimeError, match="single request"):
        response(environ, cast("StartResponse", noop_start_response))


def test_empty_segment_list_completes_immediately(socket_pair: tuple[socket.socket, socket.socket]):
    server_socket, _ = socket_pair
    response = SegmentedResponse([])
    reader = ResponseReader(response, server_socket)
    reader.start()

    response.wait_for_completion()
    reader.join()
    assert reader.items == []
    with pytest.raises(ValueError, match="between 1 and 0"):
        response.wait_until_sent(1)


def drive_response(response: SegmentedResponse, peer_socket: socket.socket) -> threading.Thread:
    environ = {"werkzeug.socket": peer_socket, "REQUEST_METHOD": "GET"}
    app_iter = response(environ, cast("StartResponse", noop_start_response))

    def run() -> None:
        try:
            list(app_iter)
        finally:
            close = getattr(app_iter, "close", None)
            if close is not None:
                close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_server_tracks_and_untracks_active_responses(socket_pair: tuple[socket.socket, socket.socket]):
    server_socket, _ = socket_pair
    server = HTTPServer()
    response = SegmentedResponse([b"a", b"b"], timeout=5)
    server._register_response(response)  # noqa: SLF001
    assert response in server._active_segmented_responses  # noqa: SLF001

    thread = drive_response(response, server_socket)
    response.wait_until_sent(1)
    server._abort_active_segmented_responses()  # noqa: SLF001
    response.wait_for_completion()
    thread.join(5)

    assert not thread.is_alive()
    assert response.aborted
    assert response not in server._active_segmented_responses  # noqa: SLF001


def test_clear_aborts_held_segmented_response(socket_pair: tuple[socket.socket, socket.socket]):
    server_socket, _ = socket_pair
    server = HTTPServer()
    response = SegmentedResponse([b"x", b"y"], timeout=5)
    server._register_response(response)  # noqa: SLF001

    thread = drive_response(response, server_socket)
    response.wait_until_sent(1)
    server.clear()
    response.wait_for_completion()
    thread.join(5)

    assert not thread.is_alive()
    assert response.aborted
    assert server._active_segmented_responses == set()  # noqa: SLF001
