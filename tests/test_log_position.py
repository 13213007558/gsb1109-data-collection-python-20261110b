import http.client
import threading
from collections.abc import Generator

import pytest
import requests
from werkzeug import Request
from werkzeug import Response

from pytest_httpserver import HTTPServer
from pytest_httpserver import InvalidLogPositionError
from pytest_httpserver import LogPosition
from pytest_httpserver import RequestMatcher


@pytest.fixture
def server() -> Generator[HTTPServer, None, None]:
    httpserver = HTTPServer()
    httpserver.start()
    yield httpserver
    httpserver.clear()
    if httpserver.is_running():
        httpserver.stop()


def test_position_ignores_warmup_requests() -> None:
    # A startup warm-up GET (the readiness probe) is logged before the test
    # logic starts.
    httpserver = HTTPServer(startup_timeout=10)
    httpserver.start()
    try:
        httpserver.expect_request("/submit", method="POST").respond_with_data("accepted")

        # the readiness probe is part of the shared log
        assert len(httpserver.log) == 1

        # "view from here": everything submitted after this position only
        position = httpserver.log_position()

        assert requests.post(httpserver.url_for("/submit"), data="payload").text == "accepted"

        # the warm-up request is still in the shared log ...
        assert len(httpserver.log) == 2

        # ... but assertions scoped to the position only see the new request
        httpserver.assert_request_made(RequestMatcher("/submit", method="POST"), position=position)
        assert httpserver.get_matching_requests_count(RequestMatcher("/"), position=position) == 0
        assert [request.path for request, _ in httpserver.iter_log(position)] == ["/submit"]
    finally:
        httpserver.clear()
        httpserver.stop()


def test_position_is_saveable_and_reusable(server: HTTPServer) -> None:
    server.expect_request("/foo").respond_with_data("OK")
    requests.get(server.url_for("/foo"))

    saved_positions: list[LogPosition] = [server.log_position()]

    requests.get(server.url_for("/foo"))
    requests.get(server.url_for("/foo"))

    position = saved_positions[0]
    assert position.is_valid()

    # querying repeatedly returns the same entries and never consumes them
    first_pass = list(server.iter_matching_requests(RequestMatcher("/foo"), position))
    second_pass = list(server.iter_matching_requests(RequestMatcher("/foo"), position))
    assert len(first_pass) == 2
    assert second_pass == first_pass

    # the shared log is left intact
    assert len(server.log) == 3


def test_two_observers_get_independent_results(server: HTTPServer) -> None:
    server.expect_request("/foo").respond_with_data("OK")
    requests.get(server.url_for("/foo"))

    observer_one = server.log_position()

    requests.get(server.url_for("/foo"))
    observer_two = server.log_position()

    requests.get(server.url_for("/foo"))

    # each observer sees only the entries appended after its own position
    assert server.get_matching_requests_count(RequestMatcher("/foo"), observer_one) == 2
    assert server.get_matching_requests_count(RequestMatcher("/foo"), observer_two) == 1

    # neither observer mutates the shared log
    assert len(server.log) == 3
    assert server.get_matching_requests_count(RequestMatcher("/foo")) == 3


def test_iter_log_uses_completion_order_for_parallel_requests() -> None:
    httpserver = HTTPServer(threaded=True)
    httpserver.start()

    slow_handler_started = threading.Event()
    release_slow_handler = threading.Event()

    def slow_handler(_request: Request) -> Response:
        slow_handler_started.set()
        release_slow_handler.wait(timeout=5)
        return Response("slow")

    httpserver.expect_request("/slow").respond_with_handler(slow_handler)
    httpserver.expect_request("/fast").respond_with_data("fast")

    try:
        position = httpserver.log_position()

        slow_conn = http.client.HTTPConnection(httpserver.host, httpserver.port)
        fast_conn = http.client.HTTPConnection(httpserver.host, httpserver.port)

        # /slow arrives first but its handler blocks ...
        slow_conn.request("GET", "/slow", headers={"Host": httpserver.host})
        assert slow_handler_started.wait(timeout=5)

        # ... /fast arrives later yet completes first
        fast_conn.request("GET", "/fast", headers={"Host": httpserver.host})
        assert fast_conn.getresponse().read() == b"fast"

        release_slow_handler.set()
        assert slow_conn.getresponse().read() == b"slow"

        slow_conn.close()
        fast_conn.close()

        # the log records completion order, not arrival order
        paths = [request.path for request, _ in httpserver.iter_log(position)]
        assert paths == ["/fast", "/slow"]
    finally:
        httpserver.clear()
        httpserver.stop()


def test_position_invalidated_by_clear_log(server: HTTPServer) -> None:
    server.expect_request("/foo").respond_with_data("OK")
    requests.get(server.url_for("/foo"))

    position = server.log_position()
    assert position.is_valid()

    server.clear_log()

    assert not position.is_valid()

    matcher = RequestMatcher("/foo")
    with pytest.raises(InvalidLogPositionError):
        list(server.iter_log(position))
    with pytest.raises(InvalidLogPositionError):
        list(server.iter_matching_requests(matcher, position))
    with pytest.raises(InvalidLogPositionError):
        server.get_matching_requests_count(matcher, position)
    with pytest.raises(InvalidLogPositionError):
        server.assert_request_made(matcher, position=position)


def test_position_invalidated_by_clear(server: HTTPServer) -> None:
    position = server.log_position()
    server.clear()
    assert not position.is_valid()
    with pytest.raises(InvalidLogPositionError):
        list(server.iter_log(position))


def test_position_does_not_become_valid_again_after_reset(server: HTTPServer) -> None:
    server.expect_request("/foo").respond_with_data("OK")
    position = server.log_position()

    server.clear_log()
    requests.get(server.url_for("/foo"))

    assert not position.is_valid()
    with pytest.raises(InvalidLogPositionError):
        list(server.iter_log(position))


def test_position_from_another_server_is_invalid() -> None:
    first = HTTPServer()
    second = HTTPServer()
    first.start()
    second.start()
    try:
        position = first.log_position()

        assert not second.is_log_position_valid(position)
        with pytest.raises(InvalidLogPositionError):
            list(second.iter_log(position))
    finally:
        first.clear()
        second.clear()
        first.stop()
        second.stop()


def test_position_repr(server: HTTPServer) -> None:
    position = server.log_position()
    assert repr(position) == "<LogPosition offset=0 valid=True>"
    server.clear_log()
    assert repr(position) == "<LogPosition offset=0 valid=False>"


def test_queries_without_position_keep_full_log_behavior(server: HTTPServer) -> None:
    server.expect_request("/foo").respond_with_data("OK")
    requests.get(server.url_for("/foo"))

    position = server.log_position()
    requests.get(server.url_for("/foo"))

    # explicit None behaves like the historical, un-scoped API
    assert len(list(server.iter_log())) == 2
    assert len(list(server.iter_log(None))) == 2
    assert server.get_matching_requests_count(RequestMatcher("/foo")) == 2
    assert server.get_matching_requests_count(RequestMatcher("/foo"), None) == 2

    # the position still scopes to the entries after it
    assert len(list(server.iter_log(position))) == 1
