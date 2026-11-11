import threading

import requests

from pytest_httpserver import HTTPServer
from pytest_httpserver import SegmentedResponse


def test_collector_does_not_ingest_partial_csv(httpserver: HTTPServer):
    response = SegmentedResponse(
        ["id,name\r\n", "1,alice\r\n", "2,bob\r\n"],
        content_type="text/csv",
    )
    httpserver.expect_oneshot_request("/csv").respond_with_response(response)

    ingested: list[bytes] = []

    def collect() -> None:
        # A streaming collector that only commits once the whole body arrived.
        with requests.get(httpserver.url_for("/csv"), stream=True, timeout=5) as result:
            result.raise_for_status()
            chunks = [chunk for chunk in result.iter_content(chunk_size=None)]
        ingested.append(b"".join(chunks))

    collector = threading.Thread(target=collect)
    collector.start()

    # Wait for the first segment instead of sleeping: only the header row is
    # out, so the collector must not have committed anything yet.
    response.wait_until_sent(1)
    assert ingested == []

    # Let the remaining segments through; only now is the body complete.
    response.release_all()
    collector.join()
    assert ingested == [b"id,name\r\n1,alice\r\n2,bob\r\n"]


def test_client_disconnect_aborts_segmented_response(httpserver: HTTPServer):
    response = SegmentedResponse(
        ["id,name\r\n", "1,alice\r\n"],
        content_type="text/csv",
    )
    httpserver.expect_oneshot_request("/csv").respond_with_response(response)

    with requests.get(httpserver.url_for("/csv"), stream=True, timeout=5):
        # The collector gives up after reading the first segment, before the
        # rest of the body is released.
        response.wait_until_sent(1)

    # Closing the response (and the connection) unblocks the held response, so
    # fixture teardown never waits for a release that never arrives.
    response.wait_for_completion()
    assert response.aborted
