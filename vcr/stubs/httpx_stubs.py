"""VCR stubs for httpx transports.

The httpx module to patch against is passed in (``httpx`` or its interoperable
fork ``httpx2``) rather than imported at module level, so a single stub serves
both. Only the ``Response`` / ``ByteStream`` classes and the ``request_context``
helper differ between the two; everything else operates on the duck-typed
request/response.
"""

import functools
import logging
from collections import defaultdict
from contextlib import suppress

from vcr.errors import CannotOverwriteExistingCassetteException
from vcr.filters import decode_response
from vcr.failures import (
    BEFORE_RESPONSE,
    DURING_BODY,
    is_before_response_failure,
    make_failure,
    playback_exception,
)
from vcr.request import Request as VcrRequest
from vcr.serializers.compat import convert_body_to_bytes

_logger = logging.getLogger(__name__)


def _failing_stream_class(httpx):
    """Build a failing stream class for an httpx (or httpx2) module.

    Like httpx's own ``ByteStream`` it is both a sync and an async stream, so a
    single replayed ``Response`` works on ``Client`` and ``AsyncClient``.
    Subclassing the module's bases keeps ``Response.close()``/``aclose()``
    working. The class is cached per module so its identity stays stable.
    """
    cache = getattr(_failing_stream_class, "_cache", {})
    if httpx not in cache:

        class _FailingByteStream(httpx.SyncByteStream, httpx.AsyncByteStream):
            def __init__(self, body, failure):
                self._body = body
                self._failure = failure

            def __iter__(self):
                yield self._body
                raise playback_exception(self._failure)

            async def __aiter__(self):
                yield self._body
                raise playback_exception(self._failure)

            def close(self):
                pass

            async def aclose(self):
                pass

        cache[httpx] = _FailingByteStream
        _failing_stream_class._cache = cache
    return cache[httpx]


def _serialize_headers(real_response):
    """
    Some headers can appear multiple times, like "Set-Cookie".
    Therefore serialize every header key to a list of values.
    """

    headers = defaultdict(list)

    for name, value in real_response.headers.multi_items():
        headers[name].append(value)

    return dict(headers)


def _serialize_response(real_response, real_response_content):
    return {
        "status": {"code": real_response.status_code, "message": real_response.reason_phrase},
        "headers": _serialize_headers(real_response),
        "body": {"string": real_response_content},
    }


def _deserialize_headers(headers):
    """
    httpx accepts headers as list of tuples of header key and value.
    """

    return [(name, value) for name, values in headers.items() for value in values]


def _deserialize_response(vcr_response, httpx):
    # A response that failed while its body was delivered replays the partial
    # body and then raises the recorded read error at the end of it.
    failure = vcr_response.get("failure")
    failing_stream = bool(failure)
    # Cassette format generated for HTTPX requests by older versions of
    # vcrpy. We restructure the content to resemble what a regular
    # cassette looks like.
    if "status_code" in vcr_response:
        vcr_response = decode_response(
            convert_body_to_bytes(
                {
                    "headers": vcr_response["headers"],
                    "body": {"string": vcr_response["content"]},
                    "status": {"code": vcr_response["status_code"]},
                },
            ),
        )
        extensions = None
    else:
        # Cassettes recorded before 8.2.0 have no reason phrase. Leave
        # extensions unset for those, as the httpcore stub that recorded them
        # did, so httpx derives the phrase from the status code.
        message = vcr_response["status"]["message"]
        extensions = {"reason_phrase": message.encode("ascii")} if message else None

    body = vcr_response["body"]["string"]
    if failing_stream:
        stream = _failing_stream_class(httpx)(body, failure)
    else:
        # Don't use content, because that closes the response,
        # which we do not want as the real response is unclosed
        stream = httpx.ByteStream(body)

    return httpx.Response(
        vcr_response["status"]["code"],
        headers=_deserialize_headers(vcr_response["headers"]),
        stream=stream,
        extensions=extensions,
    )


def _make_vcr_request(real_request, real_request_body):
    return VcrRequest(real_request.method, str(real_request.url), real_request_body, real_request.headers)


def _vcr_request(cassette, real_request, real_request_body, httpx):
    vcr_request = _make_vcr_request(real_request, real_request_body)

    if cassette.can_play_response_for(vcr_request):
        return vcr_request, _play_responses(cassette, vcr_request, httpx)

    if cassette.write_protected and cassette.filter_request(vcr_request):
        raise CannotOverwriteExistingCassetteException(cassette=cassette, failed_request=vcr_request)

    _logger.info("%s not in cassette, sending to real server", vcr_request)

    return vcr_request, None


def _record_responses(cassette, vcr_request, real_response, real_response_content):
    cassette.append(vcr_request, _serialize_response(real_response, real_response_content))


def _play_responses(cassette, vcr_request, httpx):
    vcr_response = cassette.play_response(vcr_request)
    # A failure recorded before any response raises instead of returning one.
    if is_before_response_failure(vcr_response):
        raise playback_exception(vcr_response["failure"])
    return _deserialize_response(vcr_response, httpx)


def _before_response_failure(exc):
    """A cassette interaction for a request that failed before any response."""
    return {
        "status": {"code": None, "message": None},
        "headers": {},
        "body": {"string": b""},
        "failure": make_failure(exc, BEFORE_RESPONSE),
    }


def _during_body_failure(real_response, chunks, exc):
    """A cassette interaction for a response cut off while its body was read."""
    return {
        "status": {"code": real_response.status_code, "message": real_response.reason_phrase},
        "headers": _serialize_headers(real_response),
        "body": {"string": b"".join(chunks)},
        "failure": make_failure(exc, DURING_BODY),
    }


async def _vcr_handle_async_request(cassette, real_handle_async_request, self, real_request, httpx):
    # Reading the request stream consumes the iterator, so we need to restore it afterwards
    real_request_body = b"".join([part async for part in real_request.stream])
    real_request.stream = httpx.ByteStream(real_request_body)

    vcr_request, vcr_response = _vcr_request(cassette, real_request, real_request_body, httpx)

    if vcr_response:
        return vcr_response

    try:
        real_response = await real_handle_async_request(self, real_request)
    except Exception as exc:
        # The request failed before any response was returned.
        cassette.append(vcr_request, _before_response_failure(exc))
        raise

    chunks = []
    try:
        async for part in real_response.stream:
            chunks.append(part)
    except BaseException as exc:
        # The client has not received this response and cannot close its stream yet.
        with suppress(Exception):
            await real_response.stream.aclose()
        # Cancellation (a BaseException, not Exception) is not recorded.
        if isinstance(exc, Exception):
            cassette.append(vcr_request, _during_body_failure(real_response, chunks, exc))
        raise
    real_response_content = b"".join(chunks)

    # Close the original stream so that the connection is released back to the connection pool
    with httpx._exceptions.request_context(request=real_response._request):
        await real_response.stream.aclose()

    # Reading the response stream consumes the iterator, so we need to restore it
    real_response.stream = httpx.ByteStream(real_response_content)

    _record_responses(cassette, vcr_request, real_response, real_response_content)

    return real_response


def vcr_handle_async_request(cassette, real_handle_async_request, httpx):
    @functools.wraps(real_handle_async_request)
    def _inner_handle_async_request(self, real_request):
        return _vcr_handle_async_request(cassette, real_handle_async_request, self, real_request, httpx)

    return _inner_handle_async_request


def _vcr_handle_request(cassette, real_handle_request, self, real_request, httpx):
    # Reading the request stream consumes the iterator, so we need to restore it afterwards
    real_request_body = b"".join(real_request.stream)
    real_request.stream = httpx.ByteStream(real_request_body)

    vcr_request, vcr_response = _vcr_request(cassette, real_request, real_request_body, httpx)

    if vcr_response:
        return vcr_response

    try:
        real_response = real_handle_request(self, real_request)
    except Exception as exc:
        # The request failed before any response was returned.
        cassette.append(vcr_request, _before_response_failure(exc))
        raise

    chunks = []
    try:
        for part in real_response.stream:
            chunks.append(part)
    except BaseException as exc:
        # The client has not received this response and cannot close its stream yet.
        with suppress(Exception):
            real_response.stream.close()
        # Cancellation (a BaseException, not Exception) is not recorded.
        if isinstance(exc, Exception):
            cassette.append(vcr_request, _during_body_failure(real_response, chunks, exc))
        raise
    real_response_content = b"".join(chunks)

    # Close the original stream so that the connection is released back to the connection pool
    with httpx._exceptions.request_context(request=real_response._request):
        real_response.stream.close()

    # Reading the response stream consumes the iterator, so we need to restore it
    real_response.stream = httpx.ByteStream(real_response_content)

    _record_responses(cassette, vcr_request, real_response, real_response_content)

    return real_response


def vcr_handle_request(cassette, real_handle_request, httpx):
    @functools.wraps(real_handle_request)
    def _inner_handle_request(self, real_request):
        return _vcr_handle_request(cassette, real_handle_request, self, real_request, httpx)

    return _inner_handle_request
