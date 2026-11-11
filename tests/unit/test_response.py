import http.client
import io

import pytest

from vcr.stubs import VCRHTTPResponse


def _truncated(body, failure_type="http.client.IncompleteRead", message="truncated"):
    return {
        "status": {"message": "OK", "code": 200},
        "headers": {},
        "body": {"string": body},
        "failure": {"type": failure_type, "message": message, "phase": "during_body"},
    }


def test_replayed_response_can_release_connection():
    response = VCRHTTPResponse(
        {
            "status": {"message": "OK", "code": 200},
            "headers": {},
            "body": {"string": b"recorded body"},
        },
    )

    assert response.read() == b"recorded body"
    assert response.release_conn() is None


def test_response_should_have_headers_field():
    recorded_response = {
        "status": {"message": "OK", "code": 200},
        "headers": {
            "content-length": ["0"],
            "server": ["gunicorn/18.0"],
            "connection": ["Close"],
            "access-control-allow-credentials": ["true"],
            "date": ["Fri, 24 Oct 2014 18:35:37 GMT"],
            "access-control-allow-origin": ["*"],
            "content-type": ["text/html; charset=utf-8"],
        },
        "body": {"string": b""},
    }
    response = VCRHTTPResponse(recorded_response)

    assert response.headers is not None


def test_response_headers_should_be_equal_to_msg():
    recorded_response = {
        "status": {"message": b"OK", "code": 200},
        "headers": {
            "content-length": ["0"],
            "server": ["gunicorn/18.0"],
            "connection": ["Close"],
            "content-type": ["text/html; charset=utf-8"],
        },
        "body": {"string": b""},
    }
    response = VCRHTTPResponse(recorded_response)

    assert response.headers == response.msg


def test_response_headers_should_have_correct_values():
    recorded_response = {
        "status": {"message": "OK", "code": 200},
        "headers": {
            "content-length": ["10806"],
            "date": ["Fri, 24 Oct 2014 18:35:37 GMT"],
            "content-type": ["text/html; charset=utf-8"],
        },
        "body": {"string": b""},
    }
    response = VCRHTTPResponse(recorded_response)

    assert response.headers.get("content-length") == "10806"
    assert response.headers.get("date") == "Fri, 24 Oct 2014 18:35:37 GMT"


def test_response_parses_correctly_and_fp_attribute_error_is_not_thrown():
    """
    Regression test for https://github.com/kevin1024/vcrpy/issues/440
    :return:
    """
    recorded_response = {
        "status": {"message": "OK", "code": 200},
        "headers": {
            "content-length": ["0"],
            "server": ["gunicorn/18.0"],
            "connection": ["Close"],
            "access-control-allow-credentials": ["true"],
            "date": ["Fri, 24 Oct 2014 18:35:37 GMT"],
            "access-control-allow-origin": ["*"],
            "content-type": ["text/html; charset=utf-8"],
        },
        "body": {
            "string": b"\nPMID- 19416910\nOWN - NLM\nSTAT- MEDLINE\nDA  - 20090513\nDCOM- "
            b"20090622\nLR  - "
            b"20141209\nIS  - 1091-6490 (Electronic)\nIS  - 0027-8424 (Linking)\nVI  - "
            b"106\nIP  - "
            b"19\nDP  - 2009 May 12\nTI  - Genetic dissection of histone deacetylase "
            b"requirement in "
            b"tumor cells.\nPG  - 7751-5\nLID - 10.1073/pnas.0903139106 [doi]\nAB  - "
            b"Histone "
            b"deacetylase inhibitors (HDACi) represent a new group of drugs currently\n "
            b"     being "
            b"tested in a wide variety of clinical applications. They are especially\n  "
            b"    effective "
            b"in preclinical models of cancer where they show antiproliferative\n      "
            b"action in many "
            b"different types of cancer cells. Recently, the first HDACi was\n      "
            b"approved for the "
            b"treatment of cutaneous T cell lymphomas. Most HDACi currently in\n      "
            b"clinical ",
        },
    }
    vcr_response = VCRHTTPResponse(recorded_response)
    handle = io.TextIOWrapper(vcr_response, encoding="utf-8")
    handle = iter(handle)
    articles = list(handle)
    assert len(articles) > 1


def test_replayed_response_decodes_json():
    response = VCRHTTPResponse(
        {
            "status": {"message": "OK", "code": 200},
            "headers": {"Content-Type": ["application/json"]},
            "body": {"string": b'{"answer": 42}'},
        },
    )

    assert response.json() == {"answer": 42}


@pytest.mark.parametrize(
    "read_sizes",
    [
        # The failure point is a byte position, not a read-call count: every
        # partition of the body ends at the same recorded failure.
        [3, 3, 3, 1],
        [5, 2, 10],
        [1, 1, 1, 1, 1, 1, 1, 1],
        [100, 1],
    ],
)
def test_truncated_body_fails_at_a_fixed_position(read_sizes):
    response = VCRHTTPResponse(_truncated(b"abcdefg"))
    received = b""
    with pytest.raises(http.client.IncompleteRead):
        for size in read_sizes:
            received += response.read(size)
    assert received == b"abcdefg"
    # Once raised, the failure persists on later reads.
    with pytest.raises(http.client.IncompleteRead):
        response.read(1)


def test_truncated_body_failure_is_stable_across_response_instances():
    # Two independent replayers with different read sizes fail at the same offset.
    def consume(sizes):
        response = VCRHTTPResponse(_truncated(b"abcdefg"))
        received = b""
        for size in sizes:
            received += response.read(size)
        with pytest.raises(http.client.IncompleteRead):
            response.read(1)
        return received

    assert consume([2, 2, 2, 2]) == b"abcdefg"
    assert consume([100]) == b"abcdefg"


def test_truncated_body_readall_and_readline_raise_after_the_partial_bytes():
    response = VCRHTTPResponse(_truncated(b"line-one\nline-two"))
    assert response.readline() == b"line-one\n"
    assert response.readall() == b"line-two"
    with pytest.raises(http.client.IncompleteRead):
        response.readall()


def test_truncated_body_read_zero_never_raises():
    response = VCRHTTPResponse(_truncated(b"abc"))
    assert response.read(0) == b""
    assert response.read() == b"abc"
    with pytest.raises(http.client.IncompleteRead):
        response.read()


def test_truncated_body_rebuilds_the_recorded_exception_type():
    response = VCRHTTPResponse(
        _truncated(b"abc", failure_type="http.client.RemoteDisconnected", message="connection reset"),
    )
    assert response.read() == b"abc"
    with pytest.raises(http.client.RemoteDisconnected, match="connection reset"):
        response.read()


def test_truncated_body_unknown_exception_falls_back_to_playback_error():
    from vcr.failures import PlaybackError

    response = VCRHTTPResponse(
        _truncated(b"abc", failure_type="some.missing.Thing", message="kaboom"),
    )
    assert response.read() == b"abc"
    with pytest.raises(PlaybackError, match="kaboom"):
        response.read()
