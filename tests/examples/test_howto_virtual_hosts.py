import requests

from pytest_httpserver import HTTPServer
from pytest_httpserver import RequestMatcher


def test_two_virtual_hosts_share_a_path(httpserver: HTTPServer) -> None:
    # One listening server emulates two sites. Group the expectations per site
    # with for_host(); the same path can then answer different bodies.
    site_a = httpserver.bake().for_host("site-a.example")
    site_b = httpserver.bake().for_host("site-b.example")

    site_a.expect_request("/export").respond_with_data("export from site A")
    site_b.expect_request("/export").respond_with_data("export from site B")

    # requests sends the authority in the Host header.
    response_a = requests.get(httpserver.url_for("/export"), headers={"Host": "site-a.example"})
    response_b = requests.get(httpserver.url_for("/export"), headers={"Host": "site-b.example"})

    assert response_a.text == "export from site A"
    assert response_b.text == "export from site B"

    # The log stays in one list, but it can be queried per site with a
    # host-scoped matcher, so expectations and assertions stay grouped by site.
    httpserver.assert_request_made(RequestMatcher("/export", host="site-a.example"))
    httpserver.assert_request_made(RequestMatcher("/export", host="site-b.example"))


def test_unregistered_host_is_rejected(httpserver: HTTPServer) -> None:
    httpserver.expect_request("/export", host="site-a.example").respond_with_data("site A")

    # A host nobody registered is not answered by site A's handler; it goes
    # through the explicit no-handler path instead.
    response = requests.get(httpserver.url_for("/export"), headers={"Host": "unknown.example"})
    assert response.status_code == httpserver.no_handler_status_code
    httpserver.clear_assertions()
