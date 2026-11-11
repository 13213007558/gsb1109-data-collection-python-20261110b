"""End-to-end playback tests for caller-declared flow groups.

These exercise the full HTTP patching stack (urllib) without touching the
network: the cassette already contains the interactions, so the requests are
answered entirely from it.
"""

import urllib.request

import pytest
import yaml

import vcr
from vcr.errors import CannotOverwriteExistingCassetteException

POLL_URL = "http://poll.test/status"
GROUPS = ("form-a", "form-b")


def _grouped_cassette_text():
    request = {"body": "x", "headers": {}, "method": "POST", "uri": POLL_URL}

    def interaction(body):
        return {
            "request": request,
            "response": {
                "status": {"code": 200, "message": "OK"},
                "headers": {},
                "body": {"string": body},
            },
        }

    return yaml.dump(
        {
            "version": 1,
            "interactions": [
                interaction("A-result"),
                interaction("B-result"),
                interaction("A-result-2"),
            ],
            "flow_groups": ["form-a", "form-b", "form-a"],
        },
    )


def _post():
    request = urllib.request.Request(POLL_URL, data=b"x", method="POST")
    return urllib.request.urlopen(request).read()


def test_concurrent_flows_get_their_own_responses(tmpdir):
    path = tmpdir.join("grouped.yaml")
    path.write(_grouped_cassette_text())
    my_vcr = vcr.VCR(flow_groups=GROUPS)

    with my_vcr.use_cassette(str(path), record_mode="none") as cassette:
        with cassette.flow_group("form-a"):
            assert _post() == b"A-result"
        with cassette.flow_group("form-b"):
            assert _post() == b"B-result"
        with cassette.flow_group("form-a"):
            assert _post() == b"A-result-2"


def test_a_group_running_out_does_not_take_the_other_groups_response(tmpdir):
    path = tmpdir.join("grouped.yaml")
    path.write(_grouped_cassette_text())
    my_vcr = vcr.VCR(flow_groups=GROUPS)

    with my_vcr.use_cassette(str(path), record_mode="none") as cassette:
        with cassette.flow_group("form-b"):
            assert _post() == b"B-result"
            # B's single interaction is now exhausted; A's must not be borrowed.
            with pytest.raises(CannotOverwriteExistingCassetteException):
                _post()

        # A's interactions are all still available.
        with cassette.flow_group("form-a"):
            assert _post() == b"A-result"
            assert _post() == b"A-result-2"
