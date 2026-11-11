import socket
import threading
from collections.abc import Iterable

import pytest

from pytest_httpserver import HTTPServer
from pytest_httpserver import SegmentedResponse


@pytest.fixture
def threaded_server() -> Iterable[HTTPServer]:
    server = HTTPServer(threaded=True)
    server.start()
    yield server
    if server.is_running():
        server.stop()


def open_raw_request(server: HTTPServer, path: str = "/csv") -> socket.socket:
    sock = socket.create_connection((server.host, server.port))
    sock.settimeout(5)
    sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {server.host}\r\nConnection: close\r\n\r\n".encode())
    return sock


def test_segments_are_delivered_on_release(httpserver: HTTPServer):
    response = SegmentedResponse(
        [b"id,name\r\n1,alice\r\n", b"2,bob\r\n", b"3,carol\r\n"],
        content_type="text/csv",
    )
    httpserver.expect_oneshot_request("/csv").respond_with_response(response)

    sock = open_raw_request(httpserver)
    try:
        response.wait_until_sent(1)
        # The second segment cannot be sent before it is released: its sent
        # event is only set by the handler thread after release() unblocks it.
        with pytest.raises(TimeoutError):
            response.wait_until_sent(2, timeout=0.05)

        data = b""
        while b"1,alice" not in data:
            data += sock.recv(4096)
        assert b"Transfer-Encoding: chunked" in data

        response.release(1)
        response.wait_until_sent(2)
        with pytest.raises(TimeoutError):
            response.wait_until_sent(3, timeout=0.05)

        response.release_all()
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()

    response.wait_for_completion()
    assert b"1,alice" in data
    assert b"2,bob" in data
    assert b"3,carol" in data
    assert not response.aborted


def test_release_all_sends_every_remaining_segment(httpserver: HTTPServer):
    response = SegmentedResponse([b"first\r\n", b"second\r\n", b"third\r\n"])
    httpserver.expect_oneshot_request("/csv").respond_with_response(response)

    sock = open_raw_request(httpserver)
    try:
        response.wait_until_sent(1)
        response.release_all()
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()

    response.wait_for_completion()
    assert b"first" in data
    assert b"second" in data
    assert b"third" in data


def test_client_disconnect_unblocks_server(threaded_server: HTTPServer):
    response = SegmentedResponse([b"first\r\n", b"second\r\n"])
    closed = threading.Event()
    response.call_on_close(closed.set)
    threaded_server.expect_oneshot_request("/csv").respond_with_response(response)

    sock = open_raw_request(threaded_server)
    response.wait_until_sent(1)

    # The client gives up before the collector would release the rest.
    sock.close()
    response.wait_for_completion()
    assert response.aborted
    assert closed.wait(5), "server did not close the aborted response"


@pytest.mark.parametrize("threaded", [False, True])
def test_stop_aborts_response_held_back_by_test_failure(threaded: bool):  # noqa: FBT001
    server = HTTPServer(threaded=threaded)
    server.start()
    response = SegmentedResponse([b"first\r\n", b"second\r\n"])
    server.expect_oneshot_request("/csv").respond_with_response(response)

    sock = open_raw_request(server)
    response.wait_until_sent(1)

    # Simulate fixture teardown while the second segment is still held:
    # stop() must return instead of waiting for a release that never comes.
    stopped = threading.Event()

    def stop_server() -> None:
        server.stop()
        stopped.set()

    thread = threading.Thread(target=stop_server, daemon=True)
    thread.start()
    assert stopped.wait(5), "server.stop() hung"
    thread.join(5)
    assert not thread.is_alive()
    assert response.aborted
    sock.close()


def test_clear_aborts_held_response(threaded_server: HTTPServer):
    response = SegmentedResponse([b"first\r\n", b"second\r\n"])
    threaded_server.expect_oneshot_request("/csv").respond_with_response(response)

    sock = open_raw_request(threaded_server)
    response.wait_until_sent(1)

    threaded_server.clear()
    response.wait_for_completion()
    assert response.aborted
    sock.close()


def test_completed_response_is_closed_by_server(httpserver: HTTPServer):
    response = SegmentedResponse([b"only\r\n"])
    closed = threading.Event()
    response.call_on_close(closed.set)
    httpserver.expect_oneshot_request("/csv").respond_with_response(response)

    sock = open_raw_request(httpserver)
    response.wait_for_completion()
    sock.close()

    assert closed.wait(5), "server did not close the completed response"
