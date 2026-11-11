import re
from queue import Queue

import pytest
import requests
from werkzeug import Request
from werkzeug.datastructures import Headers

from pytest_httpserver import BlockingHTTPServer
from pytest_httpserver import HTTPServer
from pytest_httpserver.httpserver import REDACTED
from pytest_httpserver.httpserver import RequestMatcher


def make_request(
    path: str = "/items",
    method: str = "GET",
    query_string: str = "",
    headers: Headers | None = None,
    data: bytes | str = b"",
    content_type: str | None = None,
) -> Request:
    request = Request.from_values(
        path=path,
        method=method,
        query_string=query_string,
        headers=headers,
        data=data,
        content_type=content_type,
    )
    request.get_data()
    return request


def test_diagnosis_lists_method_query_header_and_body_separately():
    server = HTTPServer()
    server.expect_request(
        "/items",
        method="POST",
        query_string={"page": "1"},
        headers={"Content-Type": "application/json"},
        json={"name": "expected"},
    ).respond_with_data("ok")

    request = make_request(
        method="GET",
        query_string="page=2",
        headers=Headers([("Content-Type", "text/plain")]),
        data=b'{"name": "actual"}',
        content_type="text/plain",
    )
    message = server.format_nohandler_diagnosis(request)

    assert "Closest matching expectations:" in message
    assert "- method: request='GET' expected='POST'" in message
    assert "- query: request=b'page=2' expected={'page': '1'}" in message
    assert "- headers:" in message
    assert "'Content-Type': 'text/plain'" in message
    assert "'Content-Type': 'application/json'" in message
    assert "- body (json):" in message


def test_diagnosis_includes_data_form_differences():
    server = HTTPServer()
    server.expect_request("/items", method="POST", data_form={"name": "expected"}).respond_with_data("ok")

    request = make_request(method="POST", data="name=actual", content_type="application/x-www-form-urlencoded")
    message = server.format_nohandler_diagnosis(request)

    assert "- body (form fields):" in message


def test_similar_uri_is_reported_but_unrelated_uri_falls_back_to_matcher_list():
    server = HTTPServer()
    server.expect_request("/items").respond_with_data("ok")

    close_request = make_request(path="/items/")
    close_message = server.format_nohandler_diagnosis(close_request)
    assert "Closest matching expectations:" in close_message
    assert "/items" in close_message

    unrelated_request = make_request(path="/somewhere-else")
    fallback_message = server.format_nohandler_diagnosis(unrelated_request)
    assert "Persistent matchers:" in fallback_message
    assert "Closest matching expectations:" not in fallback_message


def test_multiple_expectations_with_same_uri_are_all_reported():
    server = HTTPServer()
    server.expect_request("/items", method="GET", query_string={"page": "1"}).respond_with_data("ok")
    server.expect_request("/items", method="POST").respond_with_data("ok")

    message = server.format_nohandler_diagnosis(make_request())

    assert message.count("uri='/items'") == 2
    assert "2 expectations share this URI" in message
    assert "unique" not in message.lower()


def test_ordered_gate_is_reported_even_when_its_uri_does_not_resemble_request():
    server = HTTPServer()
    server.expect_ordered_request("/first").respond_with_data("ok")
    server.expect_ordered_request("/second").respond_with_data("ok")

    message = server.format_nohandler_diagnosis(make_request(path="/second"))

    assert "[ordered]" in message
    assert "uri='/first'" in message
    assert "ordered expectations are consumed in order" in message


def test_redact_named_header_hides_only_that_header():
    matcher = RequestMatcher(
        "/items",
        headers={"Authorization": "Bearer secret", "X-Tenant": "acme"},
        redact=["authorization"],
    )
    request = make_request(headers=Headers([("Authorization", "Bearer wrong"), ("X-Tenant", "other")]))

    lines = matcher.format_differences(request)
    rendered = "\n".join(lines)
    assert "secret" not in rendered
    assert "wrong" not in rendered
    assert rendered.count(REDACTED) == 2
    assert "'X-Tenant': 'other'" in rendered
    assert "'X-Tenant': 'acme'" in rendered


def test_redact_named_header_is_case_insensitive():
    matcher = RequestMatcher("/items", headers={"Authorization": "Bearer secret"}, redact=["AUTHORIZATION"])
    request = make_request(headers=Headers([("Authorization", "Bearer wrong")]))

    rendered = "\n".join(matcher.format_differences(request))
    assert "secret" not in rendered
    assert "wrong" not in rendered


def test_redact_all_hides_headers_and_body():
    matcher = RequestMatcher(
        "/items",
        headers={"Authorization": "Bearer secret"},
        data=b"secret-body",
        redact=True,
    )
    request = make_request(
        headers=Headers([("Authorization", "Bearer wrong")]),
        data=b"actual-body",
        content_type="text/plain",
    )

    rendered = "\n".join(matcher.format_differences(request))
    assert "secret" not in rendered
    assert "wrong" not in rendered
    assert "secret-body" not in rendered
    assert "actual-body" not in rendered
    assert REDACTED in rendered


def test_redaction_does_not_change_matching_results():
    matcher = RequestMatcher(
        "/items",
        headers={"Authorization": "Bearer secret"},
        data=b"body",
        redact=True,
    )
    matching_request = make_request(
        headers=Headers([("Authorization", "Bearer secret")]),
        data=b"body",
        content_type="text/plain",
    )
    failing_request = make_request(
        headers=Headers([("Authorization", "Bearer wrong")]),
        data=b"body",
        content_type="text/plain",
    )

    assert matcher.match(matching_request) is True
    assert matcher.match(failing_request) is False
    assert matcher.difference(matching_request) == []


def test_redacted_values_do_not_leak_into_repr():
    matcher = RequestMatcher("/items", headers={"Authorization": "Bearer secret"}, redact=["Authorization"])
    rendered = repr(matcher)
    assert "secret" not in rendered
    assert REDACTED in rendered


def test_format_differences_is_empty_for_a_matching_request():
    matcher = RequestMatcher("/items", method="GET")
    assert matcher.format_differences(make_request()) == []


def test_respond_nohandler_records_the_diagnosis_as_assertion():
    server = HTTPServer()
    server.expect_request(
        "/items", headers={"Authorization": "Bearer secret"}, redact=["Authorization"]
    ).respond_with_data("ok")

    response = server.respond_nohandler(make_request(headers=Headers([("Authorization", "Bearer wrong")])))

    assert response.status_code == server.no_handler_status_code
    assert len(server.assertions) == 1
    message = str(server.assertions[0])
    assert "No handler found for request" in message
    assert "Closest matching expectations:" in message
    assert "secret" not in message
    assert "wrong" not in message


def test_regexp_uri_expectation_is_treated_as_similar():
    server = HTTPServer()
    server.expect_request(re.compile(r"/items/\d+")).respond_with_data("ok")

    message = server.format_nohandler_diagnosis(make_request(path="/items/123"))
    assert "Closest matching expectations:" in message


def test_blocking_assert_request_message_uses_field_differences():
    server = BlockingHTTPServer()
    request = make_request(method="GET", query_string="page=2")
    server.request_handlers[request] = Queue()
    server.request_queue.put_nowait(request)

    with pytest.raises(AssertionError) as exc_info:
        server.assert_request("/items", method="POST", query_string={"page": "1"})

    message = str(exc_info.value)
    assert "does not match" in message
    assert "- method:" in message
    assert "- query:" in message


def test_blocking_assert_request_redacts_named_headers():
    server = BlockingHTTPServer()
    request = make_request(headers=Headers([("Authorization", "Bearer wrong")]))
    server.request_handlers[request] = Queue()
    server.request_queue.put_nowait(request)

    with pytest.raises(AssertionError) as exc_info:
        server.assert_request(
            "/items",
            headers={"Authorization": "Bearer secret"},
            redact=["Authorization"],
        )

    message = str(exc_info.value)
    assert "secret" not in message
    assert "wrong" not in message
    assert REDACTED in message


def test_nohandler_response_contains_diagnosis_over_http(httpserver: HTTPServer):
    httpserver.expect_request("/items", method="POST", query_string={"page": "1"}).respond_with_data("ok")

    response = requests.get(httpserver.url_for("/items"), params={"page": "2"})
    assert response.status_code == 500

    body = response.text
    assert "No handler found for request" in body
    assert "- method: request='GET' expected='POST'" in body
    assert "- query:" in body


def test_bake_passes_redact_through_to_the_matcher():
    server = HTTPServer()
    baked = server.bake(redact=["Authorization"])
    handler = baked.expect_request("/items", headers={"Authorization": "Bearer secret"})

    assert handler.matcher.redacted_headers == frozenset({"authorization"})
    rendered = repr(handler.matcher)
    assert "secret" not in rendered
    assert REDACTED in rendered


def test_redact_rejects_a_plain_string():
    with pytest.raises(TypeError, match="iterable of header names"):
        RequestMatcher("/items", redact="Authorization")


def test_assert_request_made_redacts_similar_request_output():
    server = HTTPServer()
    request = make_request(
        headers=Headers([("Authorization", "Bearer secret"), ("X-Tenant", "acme")]),
        data=b"body-content",
        content_type="text/plain",
    )
    server.log.append((request, None))

    with pytest.raises(AssertionError) as exc_info:
        server.assert_request_made(RequestMatcher("/items", method="POST", redact=["Authorization"]))

    message = str(exc_info.value)
    assert "secret" not in message
    assert "X-Tenant: acme" in message
    assert "Body: b'body-content'" in message
    assert REDACTED in message


def test_assert_request_made_redact_all_hides_body_and_headers():
    server = HTTPServer()
    request = make_request(
        headers=Headers([("Authorization", "Bearer secret")]),
        data=b"secret-body",
        content_type="text/plain",
    )
    server.log.append((request, None))

    with pytest.raises(AssertionError) as exc_info:
        server.assert_request_made(RequestMatcher("/items", method="POST", redact=True))

    message = str(exc_info.value)
    assert "secret" not in message
    assert "secret-body" not in message
    assert REDACTED in message


def test_assert_request_made_without_redaction_keeps_full_output():
    server = HTTPServer()
    request = make_request(headers=Headers([("X-Tenant", "acme")]))
    server.log.append((request, None))

    with pytest.raises(AssertionError) as exc_info:
        server.assert_request_made(RequestMatcher("/items", method="POST"))

    message = str(exc_info.value)
    assert "X-Tenant: acme" in message
    assert REDACTED not in message
