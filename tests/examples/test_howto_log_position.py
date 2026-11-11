import requests

from pytest_httpserver import HTTPServer
from pytest_httpserver import RequestMatcher


def test_log_position_example(httpserver: HTTPServer):
    # a warm-up request performed during startup is already in the shared log
    httpserver.expect_request("/warmup").respond_with_data("warming up")
    requests.get(httpserver.url_for("/warmup"))

    # save a position: "only look at requests after this point"
    position = httpserver.log_position()

    # the request under test happens after the position was taken
    httpserver.expect_request("/submit", method="POST").respond_with_data("accepted")
    requests.post(httpserver.url_for("/submit"), data="payload")

    # assertions scoped to the position ignore the warm-up request ...
    httpserver.assert_request_made(RequestMatcher("/submit", method="POST"), position=position)
    assert httpserver.get_matching_requests_count(RequestMatcher("/warmup"), position) == 0

    # ... while the shared log is never cleared and keeps every request
    assert len(httpserver.log) == 2

    # positions are reusable: querying them does not consume entries
    first = list(httpserver.iter_matching_requests(RequestMatcher("/submit"), position))
    second = list(httpserver.iter_matching_requests(RequestMatcher("/submit"), position))
    assert first == second

    # two observers can keep independent positions
    observer_one = httpserver.log_position()
    requests.post(httpserver.url_for("/submit"), data="one")
    observer_two = httpserver.log_position()
    requests.post(httpserver.url_for("/submit"), data="two")

    assert httpserver.get_matching_requests_count(RequestMatcher("/submit"), observer_one) == 2
    assert httpserver.get_matching_requests_count(RequestMatcher("/submit"), observer_two) == 1
