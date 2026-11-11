"""Playback-only rewriting of recorded interactions.

The transformations here change the in-memory copy of a cassette while it is
played back. The cassette file on disk is never rewritten by them, so the same
cassette can still be replayed with its original timestamps or used for audit.
"""

from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime

# Response headers that can carry an HTTP-date (RFC 9110). Moving them by one
# fixed offset keeps recorded freshness windows and delays relative to the
# replayed "now" instead of the original recording time.
_DATE_HEADERS = frozenset(("date", "expires", "retry-after"))


def coerce_playback_start(playback_start):
    """Validate the configured recording start and make it timezone-aware.

    Naive datetimes are taken as UTC, the timezone of HTTP-dates. Returns
    ``None`` when no start was configured.
    """
    if playback_start is None:
        return None
    if not isinstance(playback_start, datetime):
        raise TypeError(
            f"playback_start must be a datetime, got {type(playback_start).__name__}",
        )
    if playback_start.tzinfo is None:
        return playback_start.replace(tzinfo=UTC)
    return playback_start


def find_anchor(responses):
    """Return the parsed ``Date`` of the first response that has one.

    This is the timestamp the whole cassette is measured from when a playback
    start is configured. Returns ``None`` if no response has a parseable date.
    """
    for response in responses:
        if not isinstance(response, dict):
            continue
        for value in _header_values(response, "date"):
            parsed = _parse_http_date(value)
            if parsed is not None:
                return parsed
    return None


def shift_response_dates(response, delta):
    """Move every HTTP-date in a recorded response by ``delta``.

    ``Date`` and ``Expires`` always carry HTTP-dates. ``Retry-After`` is only
    shifted when it holds an HTTP-date: a delay given in seconds is relative and
    already means the same thing at any replay time, so it is left untouched.
    Values that cannot be parsed as dates are left as they are.
    """
    if not isinstance(response, dict):
        return response
    headers = response.get("headers")
    if not headers:
        return response
    for name, values in headers.items():
        if name.lower() in _DATE_HEADERS:
            headers[name] = [_shift_header_value(value, delta) for value in values]
    return response


def _shift_header_value(value, delta):
    parsed = _parse_http_date(value)
    if parsed is None:
        # Retry-After in seconds (e.g. "120"), or an unparseable value.
        return value
    return format_datetime(parsed + delta, usegmt=True)


def _parse_http_date(value):
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    # HTTP-dates are always in GMT, but be defensive about odd cassette input.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _header_values(response, wanted):
    headers = response.get("headers") or {}
    wanted = wanted.lower()
    for name, values in headers.items():
        if name.lower() == wanted:
            return values
    return ()
