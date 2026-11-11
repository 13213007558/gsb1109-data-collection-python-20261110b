import pytest
import requests
from werkzeug.datastructures import Headers

from pytest_httpserver import HTTPServer


def test_redact_single_header(httpserver: HTTPServer) -> None:
    # only the Authorization header value is hidden in diagnostic messages
    httpserver.expect_request(
        "/items",
        headers={"Authorization": "Bearer secret-token"},
        redact=["Authorization"],
    ).respond_with_data("ok")

    requests.get(httpserver.url_for("/items"), headers={"Authorization": "Bearer wrong-token"})

    with pytest.raises(AssertionError) as exc_info:
        httpserver.check_assertions()

    message = str(exc_info.value)
    assert "secret-token" not in message
    assert "wrong-token" not in message
    assert "<redacted>" in message


def test_redact_all_sensitive_values(httpserver: HTTPServer) -> None:
    # redact=True hides every header value and the entire body
    httpserver.expect_request(
        "/login",
        method="POST",
        headers={"Authorization": "Bearer secret-token"},
        data="password=hunter2",
        redact=True,
    ).respond_with_data("ok")

    requests.post(
        httpserver.url_for("/login"),
        headers=Headers([("Authorization", "Bearer secret-token")]),
        data="password=other",
    )

    with pytest.raises(AssertionError) as exc_info:
        httpserver.check_assertions()

    message = str(exc_info.value)
    assert "secret-token" not in message
    assert "hunter2" not in message
    assert "password=other" not in message
