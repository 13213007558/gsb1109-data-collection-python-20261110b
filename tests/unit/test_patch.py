import http.client as httplib

import pytest

from vcr import VCR
from vcr.cassette import Cassette
from vcr.context import current_cassette
from vcr.patch import PATCHER_NAMES, validate_skip_patchers


def test_validate_skip_patchers_accepts_known_names():
    assert validate_skip_patchers(["httpx"]) == ("httpx",)
    assert validate_skip_patchers(()) == ()
    assert set(validate_skip_patchers(PATCHER_NAMES)) == set(PATCHER_NAMES)


def test_validate_skip_patchers_rejects_unknown_names():
    with pytest.raises(ValueError, match="Unknown patcher"):
        validate_skip_patchers(["httpx", "carrier-pigeon"])


def test_vcr_rejects_unknown_skip_patchers():
    with pytest.raises(ValueError, match="Unknown patcher"):
        VCR(skip_patchers=["not-a-library"])


def test_cassette_rejects_unknown_skip_patchers(tmp_path):
    with pytest.raises(ValueError, match="Unknown patcher"):
        Cassette(str(tmp_path / "cassette.yaml"), skip_patchers=["nope"])


def test_default_vcr_patches_httpx(tmp_path):
    httpx = pytest.importorskip("httpx")
    original_async = httpx.AsyncHTTPTransport.handle_async_request
    original_sync = httpx.HTTPTransport.handle_request
    myvcr = VCR()
    with myvcr.use_cassette(str(tmp_path / "cassette.yaml")):
        assert httpx.AsyncHTTPTransport.handle_async_request is not original_async
        assert httpx.HTTPTransport.handle_request is not original_sync
    # Patchers are cleanly removed on exit.
    assert httpx.AsyncHTTPTransport.handle_async_request is original_async
    assert httpx.HTTPTransport.handle_request is original_sync


def test_skip_patchers_disables_httpx_only(tmp_path):
    httpx = pytest.importorskip("httpx")
    aiohttp = pytest.importorskip("aiohttp.client")
    original_async = httpx.AsyncHTTPTransport.handle_async_request
    original_sync = httpx.HTTPTransport.handle_request
    original_aiohttp = aiohttp.ClientSession._request
    original_httplib = httplib.HTTPConnection.request
    myvcr = VCR(skip_patchers=["httpx"])
    with myvcr.use_cassette(str(tmp_path / "cassette.yaml")):
        # The httpx patcher is skipped: transports keep their real methods,
        # so a layered client records only through the library underneath.
        assert httpx.AsyncHTTPTransport.handle_async_request is original_async
        assert httpx.HTTPTransport.handle_request is original_sync
        # Other patchers are unaffected.
        assert aiohttp.ClientSession._request is not original_aiohttp
        assert httplib.HTTPConnection.request is not original_httplib
    assert aiohttp.ClientSession._request is original_aiohttp
    assert httplib.HTTPConnection.request is original_httplib


def test_skip_patchers_per_cassette_override(tmp_path):
    httpx = pytest.importorskip("httpx")
    original_async = httpx.AsyncHTTPTransport.handle_async_request
    myvcr = VCR()
    with myvcr.use_cassette(str(tmp_path / "cassette.yaml"), skip_patchers=["httpx"]):
        assert httpx.AsyncHTTPTransport.handle_async_request is original_async
    with myvcr.use_cassette(str(tmp_path / "cassette2.yaml")):
        assert httpx.AsyncHTTPTransport.handle_async_request is not original_async


def test_skip_patchers_multiple_groups(tmp_path):
    httpx = pytest.importorskip("httpx")
    aiohttp = pytest.importorskip("aiohttp.client")
    original_async = httpx.AsyncHTTPTransport.handle_async_request
    original_aiohttp = aiohttp.ClientSession._request
    original_httplib = httplib.HTTPConnection.request
    myvcr = VCR(skip_patchers=["httpx", "aiohttp"])
    with myvcr.use_cassette(str(tmp_path / "cassette.yaml")):
        assert httpx.AsyncHTTPTransport.handle_async_request is original_async
        assert aiohttp.ClientSession._request is original_aiohttp
        assert httplib.HTTPConnection.request is not original_httplib


def test_contextvar_cassette_installs_the_shared_aiohttp_replacement(tmp_path):
    aiohttp = pytest.importorskip("aiohttp.client")
    from vcr import patch as vcr_patch

    myvcr = VCR(use_contextvar=True)
    with myvcr.use_cassette(str(tmp_path / "cassette.yaml"), record_mode="none"):
        # The replacement is built lazily on first use and shared by every
        # context-aware cassette rather than recreated per cassette.
        replacement = vcr_patch._AiohttpContextRequest
        assert replacement is not None
        assert aiohttp.ClientSession._request is replacement
    assert aiohttp.ClientSession._request is vcr_patch._AiohttpClientSessionRequest


def test_contextvar_cassette_is_bound_and_restored(tmp_path):
    pytest.importorskip("aiohttp")
    myvcr = VCR(use_contextvar=True)
    assert current_cassette.get() is None

    with myvcr.use_cassette(str(tmp_path / "outer.yaml"), record_mode="none") as outer:
        assert current_cassette.get() is outer
        with myvcr.use_cassette(str(tmp_path / "inner.yaml"), record_mode="none") as inner:
            assert current_cassette.get() is inner
        # Exiting the inner cassette restores the caller's outer cassette.
        assert current_cassette.get() is outer
    assert current_cassette.get() is None


def test_overlapping_context_cassettes_keep_one_replacement(tmp_path):
    aiohttp = pytest.importorskip("aiohttp.client")
    from vcr import patch as vcr_patch

    myvcr = VCR(use_contextvar=True)
    # Nested in one context, as two concurrently-open cassettes would be across
    # tasks: both install the identical replacement, so neither disturbs the
    # other's patching while entering or leaving.
    with myvcr.use_cassette(str(tmp_path / "outer.yaml"), record_mode="none"):
        replacement = vcr_patch._AiohttpContextRequest
        with myvcr.use_cassette(str(tmp_path / "inner.yaml"), record_mode="none"):
            assert aiohttp.ClientSession._request is replacement
        assert aiohttp.ClientSession._request is replacement
    assert aiohttp.ClientSession._request is vcr_patch._AiohttpClientSessionRequest


def test_default_cassette_does_not_touch_the_contextvar(tmp_path):
    pytest.importorskip("aiohttp")
    myvcr = VCR()
    assert current_cassette.get() is None
    with myvcr.use_cassette(str(tmp_path / "cassette.yaml"), record_mode="none"):
        assert current_cassette.get() is None
    assert current_cassette.get() is None
