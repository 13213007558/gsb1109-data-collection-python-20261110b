import pytest
import requests

from pytest_httpserver import HTTPServer
from pytest_httpserver.httpserver import HandlerType


def test_ordered_ok(httpserver: HTTPServer):
    httpserver.expect_ordered_request("/foobar").respond_with_data("OK foobar")
    httpserver.expect_ordered_request("/foobaz").respond_with_data("OK foobaz")

    assert len(httpserver.ordered_handlers) == 2

    # first requests should pass
    response = requests.get(httpserver.url_for("/foobar"))
    httpserver.check_assertions()
    assert response.status_code == 200
    assert response.text == "OK foobar"

    response = requests.get(httpserver.url_for("/foobaz"))
    httpserver.check_assertions()
    assert response.status_code == 200
    assert response.text == "OK foobaz"

    assert len(httpserver.ordered_handlers) == 0

    # second requests should fail due to 'oneshot' type
    assert requests.get(httpserver.url_for("/foobar")).status_code == 500
    assert requests.get(httpserver.url_for("/foobaz")).status_code == 500


def test_ordered_invalid_order(httpserver: HTTPServer):
    httpserver.expect_ordered_request("/foobar").respond_with_data("OK foobar")
    httpserver.expect_ordered_request("/foobaz").respond_with_data("OK foobaz")

    assert len(httpserver.ordered_handlers) == 2

    # these would not pass as the order is different
    # this mark the whole thing 'permanently failed' so no further requests must pass
    response = requests.get(httpserver.url_for("/foobaz"))
    assert response.status_code == 500

    response = requests.get(httpserver.url_for("/foobar"))
    assert response.status_code == 500

    # as no ordered handlers are triggered yet, these must be intact..
    assert len(httpserver.ordered_handlers) == 2


def test_ordered_groups_interleaved(httpserver: HTTPServer):
    httpserver.expect_ordered_request("/a1", ordered_group="a").respond_with_data("OK a1")
    httpserver.expect_ordered_request("/a2", ordered_group="a").respond_with_data("OK a2")
    httpserver.expect_ordered_request("/b1", ordered_group="b").respond_with_data("OK b1")
    httpserver.expect_ordered_request("/b2", ordered_group="b").respond_with_data("OK b2")

    # requests of different groups may be interleaved, but the order must be
    # kept within each group
    for path in ["/a1", "/b1", "/a2", "/b2"]:
        response = requests.get(httpserver.url_for(path))
        assert response.status_code == 200
        assert response.text == "OK " + path.lstrip("/")

    httpserver.check_assertions()
    assert len(httpserver.ordered_handler_groups) == 0


def test_ordered_groups_with_default_group(httpserver: HTTPServer):
    httpserver.expect_ordered_request("/first").respond_with_data("OK first")
    httpserver.expect_ordered_request("/a1", ordered_group="a").respond_with_data("OK a1")
    httpserver.expect_ordered_request("/second").respond_with_data("OK second")
    httpserver.expect_ordered_request("/a2", ordered_group="a").respond_with_data("OK a2")

    # the default (unnamed) group and the named groups are independent
    for path in ["/a1", "/first", "/a2", "/second"]:
        response = requests.get(httpserver.url_for(path))
        assert response.status_code == 200
        assert response.text == "OK " + path.lstrip("/")

    httpserver.check_assertions()
    assert len(httpserver.ordered_handlers) == 0
    assert len(httpserver.ordered_handler_groups) == 0


def test_ordered_groups_invalid_order(httpserver: HTTPServer):
    httpserver.expect_ordered_request("/a1", ordered_group="a").respond_with_data("OK a1")
    httpserver.expect_ordered_request("/a2", ordered_group="a").respond_with_data("OK a2")
    httpserver.expect_ordered_request("/b1", ordered_group="b").respond_with_data("OK b1")

    # /a2 is not the next expected request of group "a", so it fails and the
    # whole server gets permanently failed (as with the default group)
    response = requests.get(httpserver.url_for("/a2"))
    assert response.status_code == 500

    # the out-of-order request must not consume the expectations
    assert len(httpserver.ordered_handler_groups["a"]) == 2
    assert len(httpserver.ordered_handler_groups["b"]) == 1

    response = requests.get(httpserver.url_for("/a1"))
    assert response.status_code == 500
    assert len(httpserver.ordered_handler_groups["a"]) == 2


def test_ordered_groups_ambiguous(httpserver: HTTPServer):
    httpserver.expect_ordered_request("/same", ordered_group="a").respond_with_data("OK a")
    httpserver.expect_ordered_request("/same", ordered_group="b").respond_with_data("OK b")

    # the request matches the next handler of both groups: this is ambiguous
    # and must be reported instead of picking one group arbitrarily
    response = requests.get(httpserver.url_for("/same"))
    assert response.status_code == 500

    # no handler has been consumed
    assert len(httpserver.ordered_handler_groups["a"]) == 1
    assert len(httpserver.ordered_handler_groups["b"]) == 1

    with pytest.raises(AssertionError, match="Ambiguous ordered request"):
        httpserver.check_assertions()


def test_ordered_group_requires_ordered_handler_type():
    httpserver = HTTPServer()

    with pytest.raises(ValueError, match="ordered_group"):
        httpserver.expect_request("/foo", ordered_group="a")

    with pytest.raises(ValueError, match="ordered_group"):
        httpserver.expect_request("/foo", handler_type=HandlerType.ONESHOT, ordered_group="a")
