import http.client
import socket
from collections.abc import Iterator

import pytest
from werkzeug import Request
from werkzeug.test import EnvironBuilder

from pytest_httpserver import HTTPServer
from pytest_httpserver.httpserver import HostParts
from pytest_httpserver.httpserver import RequestMatcher
from pytest_httpserver.httpserver import format_host_authority
from pytest_httpserver.httpserver import get_request_host
from pytest_httpserver.httpserver import parse_host


def make_request(host_header: str | None = None, path: str = "/export") -> Request:
    environ = EnvironBuilder(path=path).get_environ()
    if host_header is not None:
        environ["HTTP_HOST"] = host_header
    return Request(environ)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("example.com", HostParts("example.com", None)),
        ("example.com:8080", HostParts("example.com", "8080")),
        ("EXAMPLE.com:8080", HostParts("example.com", "8080")),
        ("  example.com:8080  ", HostParts("example.com", "8080")),
        ("[2001:db8::1]", HostParts("2001:db8::1", None)),
        ("[2001:db8::1]:8080", HostParts("2001:db8::1", "8080")),
        ("[2001:DB8::1]:8080", HostParts("2001:db8::1", "8080")),
        ("2001:db8::1", HostParts("2001:db8::1", None)),
    ],
)
def test_parse_host(value: str, expected: HostParts) -> None:
    assert parse_host(value) == expected


def test_parse_host_rejects_unclosed_ipv6() -> None:
    with pytest.raises(ValueError, match="closing bracket"):
        parse_host("[2001:db8::1")


def test_invalid_host_expectation_raises() -> None:
    with pytest.raises(ValueError, match="closing bracket"):
        RequestMatcher("/export", host="[2001:db8::1")


def test_malformed_inbound_host_does_not_match_site() -> None:
    matcher = RequestMatcher("/export", host="site-a.example")
    request = make_request("[2001:db8::1")
    assert not matcher.match_host(request)
    # Host-agnostic expectations remain unaffected by the malformed authority.
    assert RequestMatcher("/export").match_host(request)


def test_format_host_authority() -> None:
    assert format_host_authority(HostParts("example.com", None)) == "example.com"
    assert format_host_authority(HostParts("example.com", "8080")) == "example.com:8080"
    assert format_host_authority(HostParts("2001:db8::1", None)) == "[2001:db8::1]"
    assert format_host_authority(HostParts("2001:db8::1", "8080")) == "[2001:db8::1]:8080"


def test_get_request_host_uses_host_header() -> None:
    request = make_request("Site-A.Example:8080")
    assert get_request_host(request) == HostParts("site-a.example", "8080")


def test_matcher_without_host_matches_any_host() -> None:
    matcher = RequestMatcher("/export")
    assert matcher.match_host(make_request("anything.example:1234"))
    assert matcher.match_host(make_request(""))


def test_matcher_bare_host_matches_any_port() -> None:
    matcher = RequestMatcher("/export", host="site-a.example")
    assert matcher.match_host(make_request("site-a.example"))
    assert matcher.match_host(make_request("site-a.example:8080"))
    assert matcher.match_host(make_request("SITE-A.example:9000"))
    assert not matcher.match_host(make_request("site-b.example:8080"))


def test_matcher_host_with_port_requires_that_port() -> None:
    matcher = RequestMatcher("/export", host="site-a.example:8080")
    assert matcher.match_host(make_request("site-a.example:8080"))
    assert not matcher.match_host(make_request("site-a.example:9000"))
    assert not matcher.match_host(make_request("site-a.example"))


def test_matcher_ipv6_host_normalizes_brackets() -> None:
    matcher = RequestMatcher("/v6", host="2001:db8::1")
    assert matcher.match_host(make_request("[2001:db8::1]:9000", "/v6"))
    assert matcher.match_host(make_request("[2001:DB8::1]", "/v6"))

    port_matcher = RequestMatcher("/v6", host="[2001:db8::1]:8080")
    assert port_matcher.match_host(make_request("[2001:db8::1]:8080", "/v6"))
    assert not port_matcher.match_host(make_request("[2001:db8::1]:9000", "/v6"))


def test_difference_reports_normalized_hosts() -> None:
    matcher = RequestMatcher("/export", host="site-a.example")
    difference = matcher.difference(make_request("site-b.example:8080"))
    assert ("host", "site-b.example:8080", "site-a.example") in difference


class TestDispatchHostRouting:
    @pytest.fixture
    def server(self) -> HTTPServer:
        # Not started: dispatch() and the handler registries work without a socket.
        return HTTPServer()

    def test_same_path_returns_different_body_per_host(self, server: HTTPServer) -> None:
        server.expect_request("/export", host="site-a.example").respond_with_data("body-a")
        server.expect_request("/export", host="site-b.example").respond_with_data("body-b")

        assert server.dispatch(make_request("site-a.example:8080")).get_data(as_text=True) == "body-a"
        assert server.dispatch(make_request("site-b.example:9000")).get_data(as_text=True) == "body-b"

    def test_unregistered_host_is_not_caught_by_other_site(self, server: HTTPServer) -> None:
        server.expect_request("/export", host="site-a.example").respond_with_data("body-a")

        response = server.dispatch(make_request("unknown.example", "/export"))
        assert response.status_code == server.no_handler_status_code
        server.clear_assertions()

    def test_host_agnostic_handler_keeps_single_site_behavior(self, server: HTTPServer) -> None:
        server.expect_request("/export").respond_with_data("generic")

        assert server.dispatch(make_request("whatever.example", "/export")).get_data(as_text=True) == "generic"
        assert server.dispatch(make_request("", "/export")).get_data(as_text=True) == "generic"

    def test_host_scoped_and_agnostic_handlers_coexist(self, server: HTTPServer) -> None:
        server.expect_request("/export").respond_with_data("generic")
        server.expect_request("/export", host="site-a.example").respond_with_data("site-a")

        assert server.dispatch(make_request("site-a.example", "/export")).get_data(as_text=True) == "site-a"
        assert server.dispatch(make_request("other.example", "/export")).get_data(as_text=True) == "generic"

    def test_port_is_significant_only_when_registered(self, server: HTTPServer) -> None:
        server.expect_request("/p", host="site-c.example:8080").respond_with_data("exact")

        assert server.dispatch(make_request("site-c.example:8080", "/p")).get_data(as_text=True) == "exact"
        mismatch = server.dispatch(make_request("site-c.example:9999", "/p"))
        assert mismatch.status_code == server.no_handler_status_code
        server.clear_assertions()

    def test_ipv6_authority_routing(self, server: HTTPServer) -> None:
        server.expect_request("/v6", host="2001:db8::1").respond_with_data("v6-any")
        server.expect_request("/v6p", host="[2001:db8::1]:8080").respond_with_data("v6-port")

        assert server.dispatch(make_request("[2001:db8::1]:9000", "/v6")).get_data(as_text=True) == "v6-any"
        assert server.dispatch(make_request("[2001:db8::1]:8080", "/v6p")).get_data(as_text=True) == "v6-port"

    def test_absolute_form_target_uses_netloc(self, server: HTTPServer) -> None:
        server.expect_request("/export", host="site-b.example").respond_with_data("body-b")

        # Werkzeug rewrites HTTP_HOST to the absolute target's netloc, even when
        # a different Host header is present; emulate that contract here.
        request = make_request("other.example:1234")
        request.environ["HTTP_HOST"] = "site-b.example:9000"

        assert server.dispatch(request).get_data(as_text=True) == "body-b"

    def test_bake_for_host_groups_expectations_by_site(self, server: HTTPServer) -> None:
        site_a = server.bake().for_host("site-a.example")
        site_b = server.bake().for_host("site-b.example")
        site_a.expect_request("/export").respond_with_data("a")
        site_b.expect_request("/export").respond_with_data("b")

        assert server.dispatch(make_request("site-a.example", "/export")).get_data(as_text=True) == "a"
        assert server.dispatch(make_request("site-b.example", "/export")).get_data(as_text=True) == "b"
        # The listen address is still reachable through attribute delegation.
        assert site_a.host == server.host

    def test_oneshot_handler_is_host_scoped(self, server: HTTPServer) -> None:
        server.expect_oneshot_request("/once", host="site-a.example").respond_with_data("once")

        wrong = server.dispatch(make_request("site-b.example", "/once"))
        assert wrong.status_code == server.no_handler_status_code
        assert len(server.oneshot_handlers) == 1
        server.clear_assertions()

        assert server.dispatch(make_request("site-a.example", "/once")).get_data(as_text=True) == "once"
        assert server.oneshot_handlers == []

    def test_ordered_handlers_follow_hosts(self, server: HTTPServer) -> None:
        server.expect_ordered_request("/export", host="site-a.example").respond_with_data("a")
        server.expect_ordered_request("/export", host="site-b.example").respond_with_data("b")

        assert server.dispatch(make_request("site-a.example", "/export")).get_data(as_text=True) == "a"
        assert server.dispatch(make_request("site-b.example", "/export")).get_data(as_text=True) == "b"

    def test_log_can_be_queried_per_host(self, server: HTTPServer) -> None:
        server.expect_request("/export", host="site-a.example").respond_with_data("a")
        server.expect_request("/export", host="site-b.example").respond_with_data("b")

        # application() (not dispatch()) is what records into the log.
        server.application(make_request("site-a.example", "/export"))
        server.application(make_request("site-a.example:9000", "/export"))
        server.application(make_request("site-b.example", "/export"))

        assert server.get_matching_requests_count(RequestMatcher("/export", host="site-a.example")) == 2
        assert server.get_matching_requests_count(RequestMatcher("/export", host="site-b.example")) == 1
        server.assert_request_made(RequestMatcher("/export", host="site-b.example"))

    def test_wrong_host_breaks_ordered_sequence(self, server: HTTPServer) -> None:
        server.expect_ordered_request("/export", host="site-a.example").respond_with_data("a")

        response = server.dispatch(make_request("site-b.example", "/export"))
        assert response.status_code == server.no_handler_status_code
        assert server.permanently_failed
        server.clear_assertions()


class TestRealServerHostRouting:
    @pytest.fixture
    def running_server(self) -> Iterator[HTTPServer]:
        server = HTTPServer()
        server.start()
        try:
            yield server
        finally:
            server.clear()
            if server.is_running():
                server.stop()

    def test_host_header_with_port_routes_by_site(self, running_server: HTTPServer) -> None:
        running_server.expect_request("/export", host="site-a.example").respond_with_data("body-a")
        running_server.expect_request("/export", host="site-b.example").respond_with_data("body-b")

        for host, body in (("site-a.example:8080", "body-a"), ("site-b.example:9000", "body-b")):
            conn = http.client.HTTPConnection(running_server.host, running_server.port)
            conn.request("GET", "/export", headers={"Host": host})
            response = conn.getresponse()
            assert response.status == 200
            assert response.read().decode() == body
            conn.close()

    def test_unregistered_host_returns_no_handler_status(self, running_server: HTTPServer) -> None:
        running_server.expect_request("/export", host="site-a.example").respond_with_data("body-a")

        conn = http.client.HTTPConnection(running_server.host, running_server.port)
        conn.request("GET", "/export", headers={"Host": "unknown.example"})
        response = conn.getresponse()
        assert response.status == running_server.no_handler_status_code
        conn.close()
        running_server.clear_assertions()

    def test_ipv6_host_header_routes(self, running_server: HTTPServer) -> None:
        running_server.expect_request("/v6", host="[2001:db8::1]:8080").respond_with_data("v6")

        conn = http.client.HTTPConnection(running_server.host, running_server.port)
        conn.request("GET", "/v6", headers={"Host": "[2001:db8::1]:8080"})
        response = conn.getresponse()
        assert response.status == 200
        assert response.read().decode() == "v6"
        conn.close()

    def test_absolute_form_request_target_routes_by_netloc(self, running_server: HTTPServer) -> None:
        running_server.expect_request("/export", host="site-b.example").respond_with_data("body-b")

        with socket.create_connection((running_server.host, running_server.port)) as sock:
            # Absolute-form target whose netloc differs from the Host header:
            # routing must follow the request target's authority.
            sock.sendall(
                b"GET http://site-b.example:9000/export HTTP/1.1\r\n"
                b"Host: other.example:1234\r\n"
                b"Connection: close\r\n\r\n"
            )
            raw = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk

        assert b"200" in raw.split(b"\r\n", 1)[0]
        assert raw.endswith(b"body-b")
