import pytest
import requests
from werkzeug import Request
from werkzeug import Response

from pytest_httpserver import HTTPServer

pytest_plugins = ("pytester",)


def test_sync_job_preview_never_submits(httpserver: HTTPServer):
    httpserver.expect_forbidden_request("/submit", method="POST", data_form={"confirm": "yes"})
    httpserver.expect_request("/preview").respond_with_data("preview page")

    for _ in range(3):
        response = requests.get(httpserver.url_for("/preview"))
        assert response.status_code == 200
        assert response.text == "preview page"

    httpserver.check_forbidden()


def test_forbidden_request_does_not_call_permissive_handler(httpserver: HTTPServer):
    side_effects = []

    def handler(request: Request) -> Response:
        side_effects.append(request)
        return Response("submitted")

    httpserver.expect_request("/submit", method="POST").respond_with_handler(handler)
    httpserver.expect_forbidden_request("/submit", method="POST")

    response = requests.post(httpserver.url_for("/submit"), data={"confirm": "yes"})

    assert response.status_code == 403
    assert side_effects == []

    assert len(httpserver.log) == 1
    logged_request, logged_response = httpserver.log[0]
    assert logged_request.path == "/submit"
    assert logged_response.status_code == 403

    with pytest.raises(AssertionError, match="Forbidden request"):
        httpserver.check_forbidden()


def test_forbidden_request_retried_is_intercepted_every_time(httpserver: HTTPServer):
    httpserver.expect_forbidden_request("/submit", method="POST")

    for _ in range(3):
        response = requests.post(httpserver.url_for("/submit"))
        assert response.status_code == 403

    assert len(httpserver.forbidden_requests) == 3
    assert len(httpserver.log) == 3

    matcher = httpserver.create_matcher("/submit", method="POST")
    httpserver.assert_request_made(matcher, count=3)

    with pytest.raises(AssertionError, match="Forbidden request"):
        httpserver.check_forbidden()


def test_forbidden_request_matches_method_and_data(httpserver: HTTPServer):
    httpserver.expect_forbidden_request("/submit", method="POST", data_form={"confirm": "yes"})
    httpserver.expect_request("/submit", method="GET").respond_with_data("form")
    httpserver.expect_request("/submit", method="POST").respond_with_data("submitted")

    assert requests.get(httpserver.url_for("/submit")).text == "form"
    assert requests.post(httpserver.url_for("/submit"), data={"confirm": "no"}).text == "submitted"

    response = requests.post(httpserver.url_for("/submit"), data={"confirm": "yes"})
    assert response.status_code == 403

    with pytest.raises(AssertionError, match="Forbidden request"):
        httpserver.check_forbidden()


def test_forbidden_request_matches_json(httpserver: HTTPServer):
    httpserver.expect_forbidden_request("/api/submit", method="POST", json={"action": "submit"})
    httpserver.expect_request("/api/submit", method="POST").respond_with_json({"ok": True})

    assert requests.post(httpserver.url_for("/api/submit"), json={"action": "preview"}).status_code == 200
    assert requests.post(httpserver.url_for("/api/submit"), json={"action": "submit"}).status_code == 403

    with pytest.raises(AssertionError, match="Forbidden request"):
        httpserver.check_forbidden()


def test_check_raises_for_forbidden_request(httpserver: HTTPServer):
    httpserver.expect_forbidden_request("/submit", method="POST")
    requests.post(httpserver.url_for("/submit"))

    with pytest.raises(AssertionError, match="Forbidden request"):
        httpserver.check()


def test_check_forbidden_clears_forbidden_requests(httpserver: HTTPServer):
    httpserver.expect_forbidden_request("/submit", method="POST")
    requests.post(httpserver.url_for("/submit"))

    with pytest.raises(AssertionError, match="Forbidden request"):
        httpserver.check_forbidden()

    assert httpserver.forbidden_requests == []
    httpserver.check_forbidden()


def test_format_matchers_lists_forbidden(httpserver: HTTPServer):
    httpserver.expect_forbidden_request("/submit", method="POST")

    assert "Forbidden matchers:" in httpserver.format_matchers()
    assert "/submit" in httpserver.format_matchers()


def test_fixture_verifies_forbidden_at_test_end(pytester: pytest.Pytester):
    pytester.makepyfile(
        """
        import requests

        def test_forbidden_hit_left_unverified(httpserver):
            httpserver.expect_forbidden_request("/submit", method="POST")
            response = requests.post(httpserver.url_for("/submit"))
            assert response.status_code == 403
        """
    )

    result = pytester.runpytest()

    result.assert_outcomes(passed=1, errors=1)
