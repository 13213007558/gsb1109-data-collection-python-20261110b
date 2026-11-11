import logging
from datetime import UTC, datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
import yaml

from vcr.cassette import Cassette
from vcr.config import VCR
from vcr.playback import coerce_playback_start, find_anchor, shift_response_dates


def _response(headers, body=""):
    return {
        "status": {"code": 200, "message": "OK"},
        "headers": headers,
        "body": {"string": body},
    }


def _cassette(tmpdir, responses):
    path = tmpdir.join("cassette.yml")
    path.write(
        yaml.dump(
            {
                "interactions": [
                    {
                        "request": {
                            "body": "",
                            "uri": f"https://example.com/{index}",
                            "method": "GET",
                            "headers": {},
                        },
                        "response": response,
                    }
                    for index, response in enumerate(responses)
                ],
            },
        ),
    )
    return path


def test_coerce_playback_start_none():
    assert coerce_playback_start(None) is None


def test_coerce_playback_start_naive_is_utc():
    assert coerce_playback_start(datetime(2026, 1, 1, 12, 0, 0)) == datetime(
        2026,
        1,
        1,
        12,
        0,
        0,
        tzinfo=UTC,
    )


def test_coerce_playback_start_keeps_timezone():
    tz = timezone(timedelta(hours=2))
    start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=tz)
    assert coerce_playback_start(start) is start


def test_coerce_playback_start_rejects_other_types():
    with pytest.raises(TypeError):
        coerce_playback_start("2026-01-01")


def test_find_anchor_uses_first_parseable_date():
    responses = [
        _response({"Date": ["not a date"]}),
        _response({"Date": ["Sat, 11 Jan 2014 18:45:11 GMT"]}),
        _response({"Date": ["Sun, 12 Jan 2014 09:00:00 GMT"]}),
    ]
    assert find_anchor(responses) == datetime(2014, 1, 11, 18, 45, 11, tzinfo=UTC)


def test_find_anchor_without_dates():
    assert find_anchor([_response({"Server": ["nginx"]})]) is None


def test_shift_moves_date_headers_by_one_offset():
    response = _response(
        {
            "Date": ["Sat, 11 Jan 2014 18:45:11 GMT"],
            "Expires": ["Sun, 12 Jan 2014 18:45:11 GMT"],
            "Server": ["nginx"],
        },
    )
    shift_response_dates(response, timedelta(days=100))

    shifted_date = datetime(2014, 1, 11, 18, 45, 11, tzinfo=UTC) + timedelta(days=100)
    shifted_expires = datetime(2014, 1, 12, 18, 45, 11, tzinfo=UTC) + timedelta(days=100)
    assert response["headers"]["Date"] == [format_datetime(shifted_date, usegmt=True)]
    assert response["headers"]["Expires"] == [format_datetime(shifted_expires, usegmt=True)]
    assert response["headers"]["Server"] == ["nginx"]


def test_shift_retry_after_date_but_not_delay_seconds():
    response = _response(
        {
            "Date": ["Sat, 11 Jan 2014 18:45:11 GMT"],
            "Retry-After": ["120"],
        },
    )
    shift_response_dates(response, timedelta(days=100))

    assert response["headers"]["Retry-After"] == ["120"]


def test_shift_retry_after_http_date_moves():
    response = _response(
        {
            "Date": ["Sat, 11 Jan 2014 18:45:11 GMT"],
            "Retry-After": ["Sun, 12 Jan 2014 18:45:11 GMT"],
        },
    )
    shift_response_dates(response, timedelta(days=100))

    expected = format_datetime(
        datetime(2014, 1, 12, 18, 45, 11, tzinfo=UTC) + timedelta(days=100),
        usegmt=True,
    )
    assert response["headers"]["Retry-After"] == [expected]


def test_shift_handles_missing_headers():
    response = _response({})
    assert shift_response_dates(response, timedelta(days=1)) is response


def test_helpers_tolerate_non_dict_responses():
    assert find_anchor(["not a response", _response({"Date": ["Sat, 11 Jan 2014 18:45:11 GMT"]})]) == (
        datetime(2014, 1, 11, 18, 45, 11, tzinfo=UTC)
    )
    assert shift_response_dates("not a response", timedelta(days=1)) == "not a response"


def test_playback_shifts_all_responses_by_same_offset(tmpdir):
    first_date = datetime(2014, 1, 11, 18, 45, 11, tzinfo=UTC)
    second_date = first_date + timedelta(hours=6, minutes=30)
    path = _cassette(
        tmpdir,
        [
            _response({"Date": [format_datetime(first_date, usegmt=True)], "Retry-After": ["120"]}),
            _response(
                {
                    "Date": [format_datetime(second_date, usegmt=True)],
                    "Expires": [format_datetime(second_date + timedelta(days=1), usegmt=True)],
                },
            ),
        ],
    )
    start = datetime(2026, 11, 11, 9, 0, 0, tzinfo=UTC)
    cassette = Cassette.load(path=str(path), playback_start=start)

    first = cassette.play_response(cassette.requests[0])
    second = cassette.play_response(cassette.requests[1])

    expected_offset = start - first_date
    assert first["headers"]["Date"] == [format_datetime(first_date + expected_offset, usegmt=True)]
    assert second["headers"]["Date"] == [format_datetime(second_date + expected_offset, usegmt=True)]
    # Relative intervals between interactions are preserved.
    assert (
        _parse(second["headers"]["Date"][0]) - _parse(first["headers"]["Date"][0]) == second_date - first_date
    )
    # A Retry-After given in seconds is not shifted.
    assert first["headers"]["Retry-After"] == ["120"]


def test_playback_does_not_mutate_stored_response_or_file(tmpdir):
    recorded_date = "Sat, 11 Jan 2014 18:45:11 GMT"
    path = _cassette(tmpdir, [_response({"Date": [recorded_date], "Retry-After": ["120"]})])
    start = datetime(2026, 11, 11, 9, 0, 0, tzinfo=UTC)

    cassette = Cassette.load(path=str(path), playback_start=start)
    played = cassette.play_response(cassette.requests[0])
    assert played["headers"]["Date"] != [recorded_date]
    # The in-memory stored interaction keeps the original timestamp.
    assert cassette.responses[0]["headers"]["Date"] == [recorded_date]

    cassette._save(force=True)
    on_disk = yaml.safe_load(path.read())
    saved_headers = on_disk["interactions"][0]["response"]["headers"]
    assert saved_headers["Date"] == [recorded_date]
    assert saved_headers["Retry-After"] == ["120"]


def test_playback_without_start_is_unchanged(tmpdir):
    recorded_date = "Sat, 11 Jan 2014 18:45:11 GMT"
    path = _cassette(tmpdir, [_response({"Date": [recorded_date]})])

    cassette = Cassette.load(path=str(path))
    assert cassette.play_response(cassette.requests[0])["headers"]["Date"] == [recorded_date]


def test_playback_start_without_any_date_warns_and_replays_unchanged(tmpdir, caplog):
    path = _cassette(tmpdir, [_response({"Server": ["nginx"], "Retry-After": ["120"]})])
    start = datetime(2026, 11, 11, 9, 0, 0, tzinfo=UTC)

    with caplog.at_level(logging.WARNING, logger="vcr.cassette"):
        cassette = Cassette.load(path=str(path), playback_start=start)

    assert cassette._playback_offset is None
    assert any("no parseable Date" in record.message for record in caplog.records)
    played = cassette.play_response(cassette.requests[0])
    assert played["headers"]["Retry-After"] == ["120"]


def test_playback_repeat_returns_consistent_shift(tmpdir):
    recorded_date = "Sat, 11 Jan 2014 18:45:11 GMT"
    path = _cassette(tmpdir, [_response({"Date": [recorded_date]})])
    start = datetime(2026, 11, 11, 9, 0, 0, tzinfo=UTC)

    cassette = Cassette.load(path=str(path), playback_start=start, allow_playback_repeats=True)
    first = cassette.play_response(cassette.requests[0])
    second = cassette.play_response(cassette.requests[0])
    assert first["headers"]["Date"] == second["headers"]["Date"]


def test_vcr_passes_playback_start_to_cassette(tmpdir):
    start = datetime(2026, 11, 11, 9, 0, 0, tzinfo=UTC)
    my_vcr = VCR()
    with my_vcr.use_cassette(str(tmpdir.join("c.yml")), playback_start=start) as cassette:
        assert cassette._playback_start == start


def test_vcr_playback_start_per_cassette_override(tmpdir):
    default_start = datetime(2020, 1, 1, tzinfo=UTC)
    override_start = datetime(2026, 11, 11, 9, 0, 0, tzinfo=UTC)
    my_vcr = VCR(playback_start=default_start)
    with my_vcr.use_cassette(str(tmpdir.join("c.yml")), playback_start=override_start) as cassette:
        assert cassette._playback_start == override_start
    with my_vcr.use_cassette(str(tmpdir.join("other.yml"))) as cassette:
        assert cassette._playback_start == default_start


def _parse(value):
    from email.utils import parsedate_to_datetime

    return parsedate_to_datetime(value)
