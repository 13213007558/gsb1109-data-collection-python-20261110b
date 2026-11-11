import pytest
import requests

from pytest_httpserver import HTTPServer


def test_no_handler_diagnosis(httpserver: HTTPServer) -> None:
    httpserver.expect_request(
        "/items",
        method="POST",
        query_string={"page": "1"},
        headers={"Content-Type": "application/json"},
        json={"name": "widget"},
    ).respond_with_data("ok")

    # the request is missing the page parameter, uses the wrong method and
    # sends a form body instead of a JSON body
    requests.post(httpserver.url_for("/items"), data={"name": "widget"})

    with pytest.raises(AssertionError) as exc_info:
        httpserver.check_assertions()

    message = str(exc_info.value)
    # the closest expectation is reported with one line per mismatching field
    assert "Closest matching expectations:" in message
    assert "- method:" in message
    assert "- query:" in message
    assert "- headers:" in message
    assert "- body (json):" in message
