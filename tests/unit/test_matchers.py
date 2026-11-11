import itertools
from unittest import mock

import pytest

from vcr import matchers, request

# the dict contains requests with corresponding to its key difference
# with 'base' request.
REQUESTS = {
    "base": request.Request("GET", "http://host.com/p?a=b", "", {}),
    "method": request.Request("POST", "http://host.com/p?a=b", "", {}),
    "scheme": request.Request("GET", "https://host.com:80/p?a=b", "", {}),
    "host": request.Request("GET", "http://another-host.com/p?a=b", "", {}),
    "port": request.Request("GET", "http://host.com:90/p?a=b", "", {}),
    "path": request.Request("GET", "http://host.com/x?a=b", "", {}),
    "query": request.Request("GET", "http://host.com/p?c=d", "", {}),
}


def assert_matcher(matcher_name):
    matcher = getattr(matchers, matcher_name)
    for k1, k2 in itertools.permutations(REQUESTS, 2):
        expecting_assertion_error = matcher_name in {k1, k2}
        if expecting_assertion_error:
            with pytest.raises(AssertionError):
                matcher(REQUESTS[k1], REQUESTS[k2])
        else:
            assert matcher(REQUESTS[k1], REQUESTS[k2]) is None


def test_uri_matcher():
    for k1, k2 in itertools.permutations(REQUESTS, 2):
        expecting_assertion_error = {k1, k2} != {"base", "method"}
        if expecting_assertion_error:
            with pytest.raises(AssertionError):
                matchers.uri(REQUESTS[k1], REQUESTS[k2])
        else:
            assert matchers.uri(REQUESTS[k1], REQUESTS[k2]) is None


req1_body = (
    b"<?xml version='1.0'?><methodCall><methodName>test</methodName>"
    b"<params><param><value><array><data><value><struct>"
    b"<member><name>a</name><value><string>1</string></value></member>"
    b"<member><name>b</name><value><string>2</string></value></member>"
    b"</struct></value></data></array></value></param></params></methodCall>"
)
req2_body = (
    b"<?xml version='1.0'?><methodCall><methodName>test</methodName>"
    b"<params><param><value><array><data><value><struct>"
    b"<member><name>b</name><value><string>2</string></value></member>"
    b"<member><name>a</name><value><string>1</string></value></member>"
    b"</struct></value></data></array></value></param></params></methodCall>"
)
boto3_bytes_headers = {
    "X-Amz-Content-SHA256": b"UNSIGNED-PAYLOAD",
    "Cache-Control": b"max-age=31536000, public",
    "X-Amz-Date": b"20191102T143910Z",
    "User-Agent": b"Boto3/1.9.102 Python/3.5.3 Linux/4.15.0-54-generic Botocore/1.12.253 Resource",
    "Content-MD5": b"GQqjEXsRqrPyxfTl99nkAg==",
    "Content-Type": b"text/plain",
    "Expect": b"100-continue",
    "Content-Length": "21",
}
chunked_headers = {
    "Transfer-Encoding": "chunked",
}


@pytest.mark.parametrize(
    "r1, r2",
    [
        (
            request.Request("POST", "http://host.com/", "123", {}),
            request.Request("POST", "http://another-host.com/", "123", {"Some-Header": "value"}),
        ),
        (
            request.Request(
                "POST",
                "http://host.com/",
                "a=1&b=2",
                {"Content-Type": "application/x-www-form-urlencoded"},
            ),
            request.Request(
                "POST",
                "http://host.com/",
                "b=2&a=1",
                {"Content-Type": "application/x-www-form-urlencoded"},
            ),
        ),
        (
            request.Request("POST", "http://host.com/", "123", {}),
            request.Request("POST", "http://another-host.com/", "123", {"Some-Header": "value"}),
        ),
        (
            request.Request(
                "POST",
                "http://host.com/",
                "a=1&b=2",
                {"Content-Type": "application/x-www-form-urlencoded"},
            ),
            request.Request(
                "POST",
                "http://host.com/",
                "b=2&a=1",
                {"Content-Type": "application/x-www-form-urlencoded"},
            ),
        ),
        (
            request.Request(
                "POST",
                "http://host.com/",
                '{"a": 1, "b": 2}',
                {"Content-Type": "application/json"},
            ),
            request.Request(
                "POST",
                "http://host.com/",
                '{"b": 2, "a": 1}',
                {"content-type": "application/json"},
            ),
        ),
        (
            request.Request(
                "POST",
                "http://host.com/",
                req1_body,
                {"User-Agent": "xmlrpclib", "Content-Type": "text/xml"},
            ),
            request.Request(
                "POST",
                "http://host.com/",
                req2_body,
                {"user-agent": "somexmlrpc", "content-type": "text/xml"},
            ),
        ),
        (
            request.Request(
                "POST",
                "http://host.com/",
                '{"a": 1, "b": 2}',
                {"Content-Type": "application/json"},
            ),
            request.Request(
                "POST",
                "http://host.com/",
                '{"b": 2, "a": 1}',
                {"content-type": "application/json"},
            ),
        ),
        (
            # special case for boto3 bytes headers
            request.Request("POST", "http://aws.custom.com/", b"123", boto3_bytes_headers),
            request.Request("POST", "http://aws.custom.com/", b"123", boto3_bytes_headers),
        ),
        (
            # chunked transfer encoding: decoded bytes versus encoded bytes
            request.Request("POST", "scheme1://host1.test/", b"123456789_123456", chunked_headers),
            request.Request(
                "GET",
                "scheme2://host2.test/",
                b"10\r\n123456789_123456\r\n0\r\n\r\n",
                chunked_headers,
            ),
        ),
        (
            # chunked transfer encoding: bytes iterator versus string iterator
            request.Request(
                "POST",
                "scheme1://host1.test/",
                iter([b"123456789_", b"123456"]),
                chunked_headers,
            ),
            request.Request("GET", "scheme2://host2.test/", iter(["123456789_", "123456"]), chunked_headers),
        ),
        (
            # chunked transfer encoding: bytes iterator versus single byte iterator
            request.Request(
                "POST",
                "scheme1://host1.test/",
                iter([b"123456789_", b"123456"]),
                chunked_headers,
            ),
            request.Request("GET", "scheme2://host2.test/", iter(b"123456789_123456"), chunked_headers),
        ),
    ],
)
def test_body_matcher_does_match(r1, r2):
    assert matchers.body(r1, r2) is None


@pytest.mark.parametrize(
    "r1, r2",
    [
        (
            request.Request("POST", "http://host.com/", '{"a": 1, "b": 2}', {}),
            request.Request("POST", "http://host.com/", '{"b": 2, "a": 1}', {}),
        ),
        (
            request.Request(
                "POST",
                "http://host.com/",
                '{"a": 1, "b": 3}',
                {"Content-Type": "application/json"},
            ),
            request.Request(
                "POST",
                "http://host.com/",
                '{"b": 2, "a": 1}',
                {"content-type": "application/json"},
            ),
        ),
        (
            request.Request("POST", "http://host.com/", req1_body, {"Content-Type": "text/xml"}),
            request.Request("POST", "http://host.com/", req2_body, {"content-type": "text/xml"}),
        ),
    ],
)
def test_body_match_does_not_match(r1, r2):
    with pytest.raises(AssertionError):
        matchers.body(r1, r2)


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "application/json"},
        {"Transfer-Encoding": "chunked"},
        {"Content-Type": "text/xml", "User-Agent": "xmlrpc"},
    ],
)
def test_body_matcher_compares_unparseable_bodies_as_bytes(headers):
    # A body that doesn't parse as its headers say must be compared as raw
    # bytes instead of crashing the matcher.
    def req(body):
        return request.Request("POST", "http://host.com/", body, headers)

    assert matchers.body(req(b"not {valid"), req(b"not {valid")) is None
    with pytest.raises(AssertionError):
        matchers.body(req(b"not {valid"), req(b"not {valid!"))


@pytest.mark.parametrize("checker, transformer", matchers._checker_transformer_pairs)
def test_checker_transformer_pairs_handle_none_body(checker, transformer):
    # Regression test for https://github.com/kevin1024/vcrpy/issues/913 :
    # some real clients (e.g. twilio-python) send a Content-Type header
    # (form-urlencoded or xmlrpc-over-text/xml) without an actual body
    # (e.g. on GET requests), so every transformer must tolerate body=None.
    assert transformer(None) is None


def test_query_matcher():
    req1 = request.Request("GET", "http://host.com/?a=b&c=d", "", {})
    req2 = request.Request("GET", "http://host.com/?c=d&a=b", "", {})
    assert matchers.query(req1, req2) is None

    req1 = request.Request("GET", "http://host.com/?a=b&a=b&c=d", "", {})
    req2 = request.Request("GET", "http://host.com/?a=b&c=d&a=b", "", {})
    req3 = request.Request("GET", "http://host.com/?c=d&a=b&a=b", "", {})
    assert matchers.query(req1, req2) is None
    assert matchers.query(req1, req3) is None


def test_matchers():
    assert_matcher("method")
    assert_matcher("scheme")
    assert_matcher("host")
    assert_matcher("port")
    assert_matcher("path")
    assert_matcher("query")


def test_evaluate_matcher_does_match():
    def bool_matcher(r1, r2):
        return True

    def assertion_matcher(r1, r2):
        assert 1 == 1

    r1, r2 = None, None
    for matcher in [bool_matcher, assertion_matcher]:
        match, assertion_msg = matchers._evaluate_matcher(matcher, r1, r2)
        assert match is True
        assert assertion_msg is None


def test_evaluate_matcher_does_not_match():
    def bool_matcher(r1, r2):
        return False

    def assertion_matcher(r1, r2):
        # This is like the "assert" statement preventing pytest to recompile it
        raise AssertionError()

    r1, r2 = None, None
    for matcher in [bool_matcher, assertion_matcher]:
        match, assertion_msg = matchers._evaluate_matcher(matcher, r1, r2)
        assert match is False
        assert not assertion_msg


def test_evaluate_matcher_does_not_match_with_assert_message():
    def assertion_matcher(r1, r2):
        # This is like the "assert" statement preventing pytest to recompile it
        raise AssertionError("Failing matcher")

    r1, r2 = None, None
    match, assertion_msg = matchers._evaluate_matcher(assertion_matcher, r1, r2)
    assert match is False
    assert assertion_msg == "Failing matcher"


def test_get_assertion_message():
    assert matchers.get_assertion_message(None) is None
    assert matchers.get_assertion_message("") == ""


def test_get_assertion_message_with_details():
    assertion_msg = "q1=1 != q2=1"
    expected = assertion_msg
    assert matchers.get_assertion_message(assertion_msg) == expected


@pytest.mark.parametrize(
    "r1, r2, expected_successes, expected_failures",
    [
        (
            request.Request("GET", "http://host.com/p?a=b", "", {}),
            request.Request("GET", "http://host.com/p?a=b", "", {}),
            ["method", "path"],
            [],
        ),
        (
            request.Request("GET", "http://host.com/p?a=b", "", {}),
            request.Request("POST", "http://host.com/p?a=b", "", {}),
            ["path"],
            ["method"],
        ),
        (
            request.Request("GET", "http://host.com/p?a=b", "", {}),
            request.Request("POST", "http://host.com/path?a=b", "", {}),
            [],
            ["method", "path"],
        ),
    ],
)
def test_get_matchers_results(r1, r2, expected_successes, expected_failures):
    successes, failures = matchers.get_matchers_results(r1, r2, [matchers.method, matchers.path])
    assert successes == expected_successes
    assert len(failures) == len(expected_failures)
    for i, expected_failure in enumerate(expected_failures):
        assert failures[i][0] == expected_failure
        assert failures[i][1] is not None


@mock.patch("vcr.matchers.get_matchers_results")
@pytest.mark.parametrize(
    "successes, failures, expected_match",
    [(["method", "path"], [], True), (["method"], ["path"], False), ([], ["method", "path"], False)],
)
def test_requests_match(mock_get_matchers_results, successes, failures, expected_match):
    mock_get_matchers_results.return_value = (successes, failures)
    r1 = request.Request("GET", "http://host.com/p?a=b", "", {})
    r2 = request.Request("GET", "http://host.com/p?a=b", "", {})
    match = matchers.requests_match(r1, r2, [matchers.method, matchers.path])
    assert match is expected_match


def test_path_matcher_treats_empty_and_root_as_equal():
    # httpx live requests use path=""; recorded cassettes often have path="/"
    r1 = request.Request("POST", "https://example.com", b"{}", {})
    r2 = request.Request("POST", "https://example.com/", b"{}", {})
    assert matchers.path(r1, r2) is None
    assert matchers.path(r2, r1) is None
    assert matchers.uri(r1, r2) is None
    assert matchers.uri(r2, r1) is None


def test_path_matcher_still_distinguishes_non_root_trailing_slash():
    r1 = request.Request("GET", "https://example.com/foo", "", {})
    r2 = request.Request("GET", "https://example.com/foo/", "", {})
    with pytest.raises(AssertionError):
        matchers.path(r1, r2)
    with pytest.raises(AssertionError):
        matchers.uri(r1, r2)


@pytest.mark.parametrize(
    ("uri1", "uri2"),
    [
        ("http://host.com/p?a=1&b=2", "http://host.com/p?b=2&a=1"),
        ("http://host.com/p?a=1#x", "http://host.com/p?a=1#y"),
        ("http://host.com?a=1&b=2", "http://host.com/?b=2&a=1"),
    ],
)
def test_uri_matcher_stays_exact_apart_from_empty_path(uri1, uri2):
    r1 = request.Request("GET", uri1, "", {})
    r2 = request.Request("GET", uri2, "", {})
    with pytest.raises(AssertionError):
        matchers.uri(r1, r2)


def test_uri_matcher_treats_empty_and_root_as_equal_with_query():
    r1 = request.Request("GET", "https://example.com?a=1", "", {})
    r2 = request.Request("GET", "https://example.com/?a=1", "", {})
    assert matchers.uri(r1, r2) is None


def test_body_matcher_explains_the_difference():
    headers = {"Content-Type": "application/json"}
    new = request.Request("POST", "http://host.com/", '{"model": "x", "prompt": "hello!"}', headers)
    recorded = request.Request("POST", "http://host.com/", '{"prompt": "hello", "model": "x"}', headers)
    with pytest.raises(AssertionError) as exc_info:
        matchers.body(new, recorded)
    message = str(exc_info.value)
    assert message.splitlines()[:2] == ["--- recorded", "+++ this request"]
    assert '-  "prompt": "hello"' in message
    assert '+  "prompt": "hello!"' in message
    # Keys in a different order aren't reported as a difference.
    assert '"model"' not in [line[1:].strip() for line in message.splitlines() if line[:1] in "+-"]


def test_raw_body_matcher_explains_the_difference():
    with pytest.raises(AssertionError, match=r"(?m)^-b$\n^\+c$"):
        matchers.raw_body(
            request.Request("POST", "http://host.com/", b"a\nc", {}),
            request.Request("POST", "http://host.com/", b"a\nb", {}),
        )


def test_body_matcher_summarizes_binary_bodies():
    with pytest.raises(
        AssertionError,
        match=r"the bodies differ \(3 bytes in this request, 4 bytes recorded\)",
    ):
        matchers.body(
            request.Request("POST", "http://host.com/", b"\xff\x00a", {}),
            request.Request("POST", "http://host.com/", b"\xff\x00ab", {}),
        )


def test_body_matcher_diff_is_capped():
    new = "\n".join(f"line {i}" for i in range(100)) + "\n" + "x" * 500
    recorded = "\n".join(f"line {i}!" for i in range(100)) + "\n" + "y" * 500
    with pytest.raises(AssertionError) as exc_info:
        matchers.body(
            request.Request("POST", "http://host.com/", new, {}),
            request.Request("POST", "http://host.com/", recorded, {}),
        )
    lines = str(exc_info.value).splitlines()
    assert len(lines) == matchers.MAX_DIFF_LINES + 1
    assert lines[-1].startswith("… (") and lines[-1].endswith(" more lines)")
    assert all(len(line) <= matchers.MAX_DIFF_LINE_LENGTH + 1 for line in lines)


def make_multipart_request(parts, boundary="boundary"):
    """Build a Request with a multipart/form-data body.

    ``parts`` is a list of ``(name, filename, content_type, content)`` tuples;
    ``filename`` and ``content_type`` may be None.
    """
    chunks = []
    for name, filename, content_type, content in parts:
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        chunks.append(f"--{boundary}\r\nContent-Disposition: {disposition}\r\n")
        if content_type is not None:
            chunks.append(f"Content-Type: {content_type}\r\n")
        chunks.append("\r\n")
        chunks.append(content)
        chunks.append("\r\n")
    chunks.append(f"--{boundary}--\r\n")
    body = b"".join(c if isinstance(c, bytes) else c.encode("utf-8") for c in chunks)
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    return request.Request("POST", "http://host.com/upload", body, headers)


FIELD = ("field", None, None, "value")
FILE_A = ("file", "a.txt", "text/plain", b"contents of a")
FILE_B = ("file", "b.txt", "text/plain", b"contents of b")


def test_multipart_matcher_ignores_boundary():
    r1 = make_multipart_request([FIELD, FILE_A], boundary="----client1boundary")
    r2 = make_multipart_request([FIELD, FILE_A], boundary="----client2boundary")
    assert matchers.multipart(r1, r2) is None


def test_multipart_matcher_matches_quoted_boundary():
    r1 = make_multipart_request([FIELD, FILE_A])
    r2 = make_multipart_request([FIELD, FILE_A])
    r2.headers["Content-Type"] = 'multipart/form-data; boundary="boundary"'
    assert matchers.multipart(r1, r2) is None


@pytest.mark.parametrize(
    "parts1, parts2",
    [
        # same field name and filename, different file content
        ([FILE_A], [("file", "a.txt", "text/plain", b"other contents")]),
        # different filename
        ([FILE_A], [("file", "c.txt", "text/plain", b"contents of a")]),
        # different part content type
        ([FILE_A], [("file", "a.txt", "application/octet-stream", b"contents of a")]),
        # different field value
        ([FIELD], [("field", None, None, "other value")]),
        # different field name
        ([FIELD], [("other", None, None, "value")]),
        # two same-named files swapped: order matters
        ([FILE_A, FILE_B], [FILE_B, FILE_A]),
        # extra part
        ([FIELD, FILE_A], [FIELD]),
    ],
)
def test_multipart_matcher_does_not_match(parts1, parts2):
    r1 = make_multipart_request(parts1, boundary="one")
    r2 = make_multipart_request(parts2, boundary="two")
    with pytest.raises(AssertionError):
        matchers.multipart(r1, r2)


def test_multipart_matcher_swapped_files_report_part_index():
    r1 = make_multipart_request([FILE_A, FILE_B])
    r2 = make_multipart_request([FILE_B, FILE_A])
    with pytest.raises(AssertionError, match=r"multipart part 0 differs"):
        matchers.multipart(r1, r2)


def test_multipart_matcher_handles_boundary_like_bytes_in_content():
    # Content containing the boundary string mid-line, a longer string with
    # the boundary as a prefix at a line start, and a bare "--" line must not
    # be split into bogus parts.
    content = b"see --boundary here\r\n--boundaryish line\r\n--\r\ntrailing --boundary"
    part = ("field", None, None, content)
    r1 = make_multipart_request([part, FILE_A], boundary="boundary")
    r2 = make_multipart_request([part, FILE_A], boundary="boundary")
    assert matchers.multipart(r1, r2) is None
    # And a real difference after the boundary-like bytes is still detected.
    changed = ("field", None, None, content + b"!")
    r3 = make_multipart_request([changed, FILE_A], boundary="boundary")
    with pytest.raises(AssertionError):
        matchers.multipart(r1, r3)


@pytest.mark.parametrize(
    "mutate",
    [
        # truncated before the closing delimiter
        lambda body: body[: body.rfind(b"\r\n--boundary--")],
        # closing delimiter missing its "--"
        lambda body: body.replace(b"\r\n--boundary--\r\n", b"\r\n--boundary\r\n"),
        # part headers never terminated
        lambda body: body.replace(b"\r\n\r\nvalue", b"\r\nvalue"),
        # body does not start with the delimiter
        lambda body: b"preamble\r\n" + body,
    ],
)
def test_multipart_matcher_rejects_unparseable_bodies(mutate):
    good = make_multipart_request([FIELD])
    bad = request.Request(
        "POST",
        "http://host.com/upload",
        mutate(good.body),
        {"Content-Type": "multipart/form-data; boundary=boundary"},
    )
    for r1, r2 in [(good, bad), (bad, good), (bad, bad)]:
        with pytest.raises(AssertionError, match="insufficient basis for multipart matching"):
            matchers.multipart(r1, r2)


def test_multipart_matcher_requires_multipart_content_type():
    r1 = request.Request("POST", "http://host.com/", b"a=1", {"Content-Type": "text/plain"})
    r2 = request.Request("POST", "http://host.com/", b"a=1", {"Content-Type": "text/plain"})
    with pytest.raises(AssertionError, match="insufficient basis for multipart matching"):
        matchers.multipart(r1, r2)


def test_multipart_matcher_is_registered():
    import vcr

    assert vcr.VCR().matchers["multipart"] is matchers.multipart
