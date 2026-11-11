from __future__ import annotations

import re
import threading
from typing import TYPE_CHECKING

import pytest
from werkzeug import Request
from werkzeug import Response
from werkzeug.test import Client

from pytest_httpserver import HandlerRegistrationError
from pytest_httpserver import HTTPServer
from pytest_httpserver import RequestHandler
from pytest_httpserver import URIPattern
from pytest_httpserver.httpserver import HandlerType

if TYPE_CHECKING:
    from collections.abc import Iterable


class ExplodingPattern(URIPattern):
    """URIPattern which fails lazily, only when a request is matched."""

    def __init__(self, label: str = "boom") -> None:
        self.label = label

    def match(self, uri: str) -> bool:
        message = f"broken rule {self.label}: {uri}"
        raise RuntimeError(message)

    def __repr__(self) -> str:
        return f"<ExplodingPattern {self.label}>"


@pytest.fixture
def server() -> Iterable[HTTPServer]:
    http_server = HTTPServer()
    yield http_server
    http_server.clear()


def make_client(http_server: HTTPServer) -> Client:
    return Client(Request.application(http_server.application))


def test_group_is_invisible_until_published(server: HTTPServer) -> None:
    group = server.expectation_group()
    group.expect_request("/one").respond_with_data("1")
    group.expect_request("/two").respond_with_data("2")

    assert server.handlers == []
    group.publish()
    assert len(server.handlers) == 2

    client = make_client(server)
    assert client.get("/one").data == b"1"
    assert client.get("/two").data == b"2"


def test_published_group_reports_matching_handler(server: HTTPServer) -> None:
    with server.expectation_group() as group:
        group.expect_request(re.compile(r"/items/\d+")).respond_with_data("item")

    client = make_client(server)
    response = client.get("/items/42")
    assert response.status_code == 200
    assert response.data == b"item"


def test_invalid_matcher_fails_publish_and_identifies_the_rule(server: HTTPServer) -> None:
    group = server.expectation_group()
    group.expect_request("/ok").respond_with_data("ok")
    group.expect_request(ExplodingPattern("middle")).respond_with_data("bad")
    group.expect_request("/ok2").respond_with_data("ok2")

    with pytest.raises(HandlerRegistrationError) as exc_info:
        group.publish()

    message = str(exc_info.value)
    assert "rule #2" in message
    assert "broken rule middle" in message
    assert server.handlers == []
    assert server.oneshot_handlers == []
    assert server.ordered_handlers == []


def test_missing_response_fails_publish_and_identifies_the_rule(server: HTTPServer) -> None:
    group = server.expectation_group()
    group.expect_request("/a").respond_with_data("a")
    group.expect_request("/b")

    with pytest.raises(HandlerRegistrationError) as exc_info:
        group.publish()

    assert "rule #2" in str(exc_info.value)
    assert server.handlers == []


def test_failed_publish_keeps_previous_rules_untouched(server: HTTPServer) -> None:
    server.expect_request("/existing").respond_with_data("existing")

    group = server.expectation_group()
    group.expect_request("/new").respond_with_data("new")
    group.expect_request(ExplodingPattern()).respond_with_data("bad")

    with pytest.raises(HandlerRegistrationError):
        group.publish()

    client = make_client(server)
    assert client.get("/existing").data == b"existing"
    assert client.get("/new").status_code == 500
    assert len(server.handlers) == 1


def test_group_supports_all_handler_types(server: HTTPServer) -> None:
    with server.expectation_group() as group:
        group.expect_request("/permanent").respond_with_data("p")
        group.expect_oneshot_request("/oneshot").respond_with_data("o")
        group.expect_ordered_request("/ordered").respond_with_data("r")

    assert len(server.handlers) == 1
    assert len(server.oneshot_handlers) == 1
    assert len(server.ordered_handlers) == 1

    client = make_client(server)
    # ordered handlers must be consumed before any other handler type
    assert client.get("/ordered").data == b"r"
    assert client.get("/oneshot").data == b"o"
    assert client.get("/permanent").data == b"p"
    assert client.get("/oneshot").status_code == 500


def test_context_manager_publishes_on_clean_exit(server: HTTPServer) -> None:
    with server.expectation_group() as group:
        group.expect_request("/foo").respond_with_data("bar")

    assert len(server.handlers) == 1
    assert make_client(server).get("/foo").data == b"bar"


def test_context_manager_does_not_publish_on_exception(server: HTTPServer) -> None:
    with (
        pytest.raises(RuntimeError),
        server.expectation_group() as group,
    ):
        group.expect_request("/foo").respond_with_data("bar")
        raise RuntimeError("boom")

    assert server.handlers == []


def test_published_group_cannot_be_reused(server: HTTPServer) -> None:
    group = server.expectation_group()
    group.expect_request("/foo").respond_with_data("bar")
    group.publish()

    with pytest.raises(HandlerRegistrationError):
        group.expect_request("/baz")
    with pytest.raises(HandlerRegistrationError):
        group.add_handler(
            RequestHandler(server.create_matcher("/qux")),
            HandlerType.PERMANENT,
        )


def test_empty_group_publishes_without_error(server: HTTPServer) -> None:
    server.expectation_group().publish()
    assert server.handlers == []


def test_handlers_property_preserves_order(server: HTTPServer) -> None:
    group = server.expectation_group()
    first = group.expect_request("/first")
    first.respond_with_data("1")
    second = group.expect_request("/second")
    second.respond_with_data("2")

    assert group.handlers == (first, second)


def test_add_handler_publishes_existing_handler(server: HTTPServer) -> None:
    handler = RequestHandler(server.create_matcher("/custom"))
    handler.respond_with_data("ok")

    group = server.expectation_group()
    group.add_handler(handler, HandlerType.ONESHOT)
    group.publish()

    assert len(server.oneshot_handlers) == 1
    assert make_client(server).get("/custom").data == b"ok"


def test_replace_swaps_the_whole_set_atomically(server: HTTPServer) -> None:
    server.expect_request("/v1").respond_with_data("old")

    group = server.expectation_group()
    group.expect_request("/v2").respond_with_data("new")
    group.publish(replace=True)

    client = make_client(server)
    assert client.get("/v2").data == b"new"
    assert client.get("/v1").status_code == 500
    assert len(server.handlers) == 1


def test_failed_replace_is_a_noop(server: HTTPServer) -> None:
    server.expect_request("/v1").respond_with_data("old")

    group = server.expectation_group()
    group.expect_request("/v2").respond_with_data("new")
    group.expect_request(ExplodingPattern()).respond_with_data("bad")

    with pytest.raises(HandlerRegistrationError):
        group.publish(replace=True)

    client = make_client(server)
    assert client.get("/v1").data == b"old"
    assert client.get("/v2").status_code == 500
    assert len(server.handlers) == 1


def test_replace_default_from_constructor(server: HTTPServer) -> None:
    server.expect_request("/v1").respond_with_data("old")

    with server.expectation_group(replace=True) as group:
        group.expect_request("/v2").respond_with_data("new")

    client = make_client(server)
    assert client.get("/v2").data == b"new"
    assert client.get("/v1").status_code == 500


def test_inflight_request_keeps_old_snapshot_during_replace(server: HTTPServer) -> None:
    entered = threading.Event()

    def slow_handler(_request: Request) -> Response:
        entered.set()
        threading.Event().wait(0.2)
        return Response("old-slow")

    server.expect_request("/slow").respond_with_handler(slow_handler)

    result: dict[str, tuple[int, bytes]] = {}

    def inflight() -> None:
        response = make_client(server).get("/slow")
        result["inflight"] = (response.status_code, response.data)

    worker = threading.Thread(target=inflight)
    worker.start()
    entered.wait()

    # the request is running its handler (lock already released) while the set is replaced
    group = server.expectation_group()
    group.expect_request("/new").respond_with_data("new")
    group.publish(replace=True)

    worker.join()
    client = make_client(server)
    assert result["inflight"] == (200, b"old-slow")
    assert client.get("/new").data == b"new"
    assert client.get("/slow").status_code == 500


def test_concurrent_requests_and_publishes_never_raise(server: HTTPServer) -> None:
    server.expect_request("/page").respond_with_data("0")

    errors: list[Exception] = []

    def worker() -> None:
        client = make_client(server)
        for _ in range(50):
            try:
                client.get("/page")
            except Exception as exc:  # noqa: BLE001 - any failure is a concurrency regression
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()

    for generation in range(1, 6):
        group = server.expectation_group()
        group.expect_request("/page").respond_with_data(str(generation))
        group.publish(replace=True)

    for thread in threads:
        thread.join()

    assert errors == []
    assert make_client(server).get("/page").data in {str(generation).encode() for generation in range(6)}


def test_group_coexists_with_per_item_registration(server: HTTPServer) -> None:
    server.expect_request("/single").respond_with_data("single")

    with server.expectation_group() as group:
        group.expect_request("/batched").respond_with_data("batched")

    client = make_client(server)
    assert client.get("/single").data == b"single"
    assert client.get("/batched").data == b"batched"
    assert len(server.handlers) == 2


def test_published_oneshot_handler_is_removed_after_use(server: HTTPServer) -> None:
    with server.expectation_group() as group:
        group.expect_oneshot_request("/once").respond_with_data("ok")

    client = make_client(server)
    assert client.get("/once").data == b"ok"
    assert server.oneshot_handlers == []
    assert client.get("/once").status_code == 500


def test_published_ordered_handler_drives_permanent_failure(server: HTTPServer) -> None:
    with server.expectation_group() as group:
        group.expect_ordered_request("/first").respond_with_data("1")
        group.expect_ordered_request("/second").respond_with_data("2")

    client = make_client(server)
    assert client.get("/second").status_code == 500  # wrong order
    assert server.permanently_failed is True
    assert client.get("/first").status_code == 500


def test_group_accepts_compiled_regexp(server: HTTPServer) -> None:
    with server.expectation_group() as group:
        group.expect_request(re.compile(r"/pages/\d+")).respond_with_data("page")

    client = make_client(server)
    assert client.get("/pages/13").data == b"page"
    assert client.get("/pages/about").status_code == 500


def test_file_generated_spec_published_as_one_batch(server: HTTPServer) -> None:
    # a long list of expectations generated from a file/declarative spec
    def load_spec() -> list[tuple[str, str]]:
        return [(f"/page/{number}", str(number)) for number in range(20)]

    group = server.expectation_group()
    for uri, body in load_spec():
        group.expect_request(uri).respond_with_data(body)
    group.publish()

    assert len(server.handlers) == 20
    client = make_client(server)
    assert client.get("/page/0").data == b"0"
    assert client.get("/page/19").data == b"19"


def test_broken_rule_in_long_spec_aborts_the_whole_batch(server: HTTPServer) -> None:
    group = server.expectation_group()
    for number in range(15):
        group.expect_request(f"/page/{number}").respond_with_data(str(number))
    group.expect_request(ExplodingPattern("late-rule")).respond_with_data("bad")
    for number in range(15, 18):
        group.expect_request(f"/page/{number}").respond_with_data(str(number))

    with pytest.raises(HandlerRegistrationError) as exc_info:
        group.publish()

    assert "rule #16" in str(exc_info.value)
    assert server.handlers == []
    assert make_client(server).get("/page/0").status_code == 500
