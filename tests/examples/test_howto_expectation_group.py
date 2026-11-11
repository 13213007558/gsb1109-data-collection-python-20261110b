import pytest
import requests

from pytest_httpserver import HandlerRegistrationError
from pytest_httpserver import HTTPServer
from pytest_httpserver import URIPattern


def test_publish_expectations_as_a_batch(httpserver: HTTPServer) -> None:
    # build a long list of expectations, for example generated from a file
    page_specs = [(f"/pages/{number}", str(number)) for number in range(20)]

    # rules collected on the group stay invisible until publish()
    with httpserver.expectation_group() as group:
        for uri, body in page_specs:
            group.expect_request(uri).respond_with_data(body)

    # the whole set becomes visible at once
    assert requests.get(httpserver.url_for("/pages/0")).text == "0"
    assert requests.get(httpserver.url_for("/pages/19")).text == "19"


class FailingPattern(URIPattern):
    def match(self, uri: str) -> bool:
        raise RuntimeError(f"this rule is broken: {uri}")


def test_invalid_batch_is_not_published(httpserver: HTTPServer) -> None:
    httpserver.expect_request("/existing").respond_with_data("existing")

    group = httpserver.expectation_group()
    group.expect_request("/new").respond_with_data("new")
    group.expect_request(FailingPattern()).respond_with_data("bad")

    # the error says which rule failed, and nothing from the group is visible
    with pytest.raises(HandlerRegistrationError, match="rule #2"):
        group.publish()

    # the previously registered rules keep serving requests untouched
    assert requests.get(httpserver.url_for("/existing")).text == "existing"
    assert requests.get(httpserver.url_for("/new")).status_code == 500


def test_replace_a_whole_set_atomically(httpserver: HTTPServer) -> None:
    httpserver.expect_request("/page").respond_with_data("page-1")

    # swap the complete set of rules for a new generation
    with httpserver.expectation_group(replace=True) as group:
        group.expect_request("/page").respond_with_data("page-2")

    assert requests.get(httpserver.url_for("/page")).text == "page-2"
