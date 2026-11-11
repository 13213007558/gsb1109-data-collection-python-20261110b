"""
Offline comparison of two cassette files, e.g. before and after re-recording
against a changed upstream.

Unlike a line-level diff of the cassette files, this compares *requests* with
VCR's own request matchers (so reordered query parameters or serialization
noise don't count as changes) and *responses* semantically (status, headers,
body), then reports which interactions were added, removed, changed, or kept.

Usage::

    python3 -m vcr.diff OLD_CASSETTE NEW_CASSETTE [--match-on method uri ...]

The report only describes the differences; it never modifies either file.
Positions in the report are 0-based indexes into each file's ``interactions``
list, so you can jump back to the interaction in either file.
"""

import argparse
import difflib
import json
import sys
from dataclasses import dataclass, field

from .config import VCR
from .matchers import MAX_DIFF_LINE_LENGTH, MAX_DIFF_LINES, requests_match
from .persisters.filesystem import FilesystemPersister
from .serializers import jsonserializer, yamlserializer

_SERIALIZERS = {"yaml": yamlserializer, "json": jsonserializer}


def _serializer_for(path, serializer=None):
    if serializer is not None:
        try:
            return _SERIALIZERS[serializer]
        except KeyError:
            raise KeyError(f"Serializer {serializer!r} doesn't exist or isn't registered") from None
    if str(path).endswith(".json"):
        return jsonserializer
    return yamlserializer


def _load_interactions(path, serializer=None):
    """Load a cassette file offline. Returns a list of (request, response)."""
    requests, responses, *_ = FilesystemPersister.load_cassette(
        path,
        serializer=_serializer_for(path, serializer),
    )
    return list(zip(requests, responses, strict=False))


def _resolve_matchers(match_on):
    """Resolve matcher names (or pass through callables) to matcher functions."""
    if match_on is None:
        match_on = VCR().match_on
    registry = VCR().matchers
    matchers = []
    for matcher in match_on:
        if callable(matcher):
            matchers.append(matcher)
        else:
            try:
                matchers.append(registry[matcher])
            except KeyError:
                raise KeyError(f"Matcher {matcher} doesn't exist or isn't registered") from None
    return matchers


def _normalized_headers(headers):
    """Headers as {lowercase name: sorted values}, so order and case don't count."""
    return {str(name).lower(): sorted(str(value) for value in values) for name, values in headers.items()}


def _response_body(response):
    body = response.get("body")
    if isinstance(body, dict):
        body = body.get("string")
    return body


def _as_json(body):
    """The body parsed as JSON, or None if it isn't JSON."""
    if isinstance(body, (bytes, bytearray)):
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(body, str) or not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def _short_diff(old_text, new_text):
    """A short unified diff of two texts, labelled old -> new."""
    diff = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            "old",
            "new",
            n=1,
            lineterm="",
        ),
    )
    lines = [
        line if len(line) <= MAX_DIFF_LINE_LENGTH else line[:MAX_DIFF_LINE_LENGTH] + "…"
        for line in diff[:MAX_DIFF_LINES]
    ]
    if len(diff) > MAX_DIFF_LINES:
        lines.append(f"… ({len(diff) - MAX_DIFF_LINES} more lines)")
    return "\n".join(lines)


def _body_text(body):
    if body is None:
        return ""
    if isinstance(body, (bytes, bytearray)):
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return body if isinstance(body, str) else str(body)


def _bodies_equal(old_body, new_body):
    """Semantic body comparison: JSON is compared structurally, so key order
    and serialization format don't count; anything else is compared as-is."""
    old_json, new_json = _as_json(old_body), _as_json(new_body)
    if old_json is not None and new_json is not None:
        return old_json == new_json
    if old_body is None or new_body is None:
        return old_body is None and new_body is None
    return old_body == new_body


def compare_responses(old_response, new_response):
    """Compare two recorded responses. Returns a list of human-readable
    differences; empty means the responses are equivalent."""
    differences = []

    old_status = old_response.get("status") or {}
    new_status = new_response.get("status") or {}
    for key in ("code", "message"):
        if old_status.get(key) != new_status.get(key):
            differences.append(f"status {key}: {old_status.get(key)!r} -> {new_status.get(key)!r}")

    old_headers = _normalized_headers(old_response.get("headers") or {})
    new_headers = _normalized_headers(new_response.get("headers") or {})
    if old_headers != new_headers:
        header_diffs = []
        for name in sorted(old_headers.keys() | new_headers.keys()):
            old_values, new_values = old_headers.get(name), new_headers.get(name)
            if old_values != new_values:
                header_diffs.append(f"{name}: {old_values} -> {new_values}")
        differences.append("headers: " + "; ".join(header_diffs))

    old_body, new_body = _response_body(old_response), _response_body(new_response)
    if not _bodies_equal(old_body, new_body):
        old_json, new_json = _as_json(old_body), _as_json(new_body)
        if old_json is not None and new_json is not None:
            # Show the structural difference, not the serialization difference.
            old_text = json.dumps(old_json, indent=2, sort_keys=True, default=str)
            new_text = json.dumps(new_json, indent=2, sort_keys=True, default=str)
        else:
            old_text, new_text = _body_text(old_body), _body_text(new_body)
        if old_text is None or new_text is None:
            differences.append(
                f"body: binary bodies differ ({_size(old_body)} -> {_size(new_body)})",
            )
        else:
            differences.append("body:\n" + _short_diff(old_text, new_text))

    return differences


def _size(body):
    try:
        return f"{len(body)} bytes"
    except TypeError:
        return "no body" if body is None else type(body).__name__


@dataclass
class Pair:
    """Two interactions matched with each other, one from each file."""

    old_index: int
    new_index: int
    request: str
    differences: list = field(default_factory=list)
    ambiguous: bool = False

    @property
    def changed(self):
        return bool(self.differences)


@dataclass
class Unmatched:
    """An interaction with no counterpart in the other file."""

    index: int
    request: str
    ambiguous: bool = False
    note: str = ""


@dataclass
class CassetteDiff:
    """The result of comparing two cassette files."""

    old_path: str
    new_path: str
    matcher_names: list
    pairs: list = field(default_factory=list)
    removed: list = field(default_factory=list)  # only in the old file
    added: list = field(default_factory=list)  # only in the new file

    @property
    def changed(self):
        return [pair for pair in self.pairs if pair.changed]

    @property
    def unchanged(self):
        return [pair for pair in self.pairs if not pair.changed]

    @property
    def ambiguous(self):
        entries = [pair for pair in self.pairs if pair.ambiguous]
        entries += [entry for entry in self.removed if entry.ambiguous]
        entries += [entry for entry in self.added if entry.ambiguous]
        return entries

    @property
    def identical(self):
        return not self.changed and not self.added and not self.removed

    def to_dict(self):
        return {
            "old_path": self.old_path,
            "new_path": self.new_path,
            "match_on": self.matcher_names,
            "changed": [
                {
                    "old_index": pair.old_index,
                    "new_index": pair.new_index,
                    "request": pair.request,
                    "ambiguous": pair.ambiguous,
                    "differences": pair.differences,
                }
                for pair in self.changed
            ],
            "unchanged": [
                {
                    "old_index": pair.old_index,
                    "new_index": pair.new_index,
                    "request": pair.request,
                    "ambiguous": pair.ambiguous,
                }
                for pair in self.unchanged
            ],
            "added": [
                {
                    "new_index": entry.index,
                    "request": entry.request,
                    "ambiguous": entry.ambiguous,
                    "note": entry.note,
                }
                for entry in self.added
            ],
            "removed": [
                {
                    "old_index": entry.index,
                    "request": entry.request,
                    "ambiguous": entry.ambiguous,
                    "note": entry.note,
                }
                for entry in self.removed
            ],
        }

    def render(self):
        lines = [
            f"Comparing {self.old_path} -> {self.new_path} (match on: {', '.join(self.matcher_names)})",
        ]
        for pair in self.pairs:
            flag = " [ambiguous pairing]" if pair.ambiguous else ""
            positions = f"old #{pair.old_index} <-> new #{pair.new_index}"
            if pair.changed:
                lines.append(f"CHANGED   {positions}  {pair.request}{flag}")
                lines.extend(f"    {difference}" for difference in pair.differences)
            else:
                lines.append(f"SAME      {positions}  {pair.request}{flag}")
        for entry in self.removed:
            flag = f" [ambiguous: {entry.note}]" if entry.ambiguous else ""
            lines.append(f"REMOVED   old #{entry.index}  {entry.request}{flag}")
        for entry in self.added:
            flag = f" [ambiguous: {entry.note}]" if entry.ambiguous else ""
            lines.append(f"ADDED     new #{entry.index}  {entry.request}{flag}")
        lines.append(
            f"Summary: {len(self.changed)} changed, {len(self.added)} added, "
            f"{len(self.removed)} removed, {len(self.unchanged)} unchanged, "
            f"{len(self.ambiguous)} ambiguous",
        )
        return "\n".join(lines)


def _match_sets(requests, other_requests, matchers):
    """For each request, the indexes of all requests in the other file that match it."""
    return [
        {j for j, other in enumerate(other_requests) if requests_match(request, other, matchers)}
        for request in requests
    ]


def compare_interactions(old_interactions, new_interactions, match_on=None):
    """Pair the interactions of two loaded cassettes and compare responses.

    Pairing is one-to-one, greedy in file order: each old interaction takes the
    first still-unpaired new interaction it matches. A request recorded twice
    therefore keeps both occurrences, each paired with a distinct counterpart.
    A pairing is flagged ambiguous when either side also matches other
    interactions, so it can't be considered reliable.
    """
    matchers = _resolve_matchers(match_on)
    matcher_names = [getattr(m, "__name__", str(m)) for m in matchers]
    old_requests = [request for request, _ in old_interactions]
    new_requests = [request for request, _ in new_interactions]
    old_matches = _match_sets(old_requests, new_requests, matchers)
    new_matches = _match_sets(new_requests, old_requests, matchers)

    pairs = []
    used_new = set()
    used_old = set()
    for old_index, candidates in enumerate(old_matches):
        available = sorted(candidates - used_new)
        if not available:
            continue
        new_index = available[0]
        used_new.add(new_index)
        used_old.add(old_index)
        differences = compare_responses(
            old_interactions[old_index][1],
            new_interactions[new_index][1],
        )
        pairs.append(
            Pair(
                old_index=old_index,
                new_index=new_index,
                request=str(old_requests[old_index]),
                differences=differences,
                ambiguous=len(old_matches[old_index]) > 1 or len(new_matches[new_index]) > 1,
            ),
        )

    removed = []
    for old_index in range(len(old_interactions)):
        if old_index in used_old:
            continue
        consumed = sorted(old_matches[old_index])
        removed.append(
            Unmatched(
                index=old_index,
                request=str(old_requests[old_index]),
                ambiguous=bool(consumed),
                note=f"matches new {consumed}, already paired" if consumed else "",
            ),
        )
    added = []
    for new_index in range(len(new_interactions)):
        if new_index in used_new:
            continue
        consumed = sorted(new_matches[new_index])
        added.append(
            Unmatched(
                index=new_index,
                request=str(new_requests[new_index]),
                ambiguous=bool(consumed),
                note=f"matches old {consumed}, already paired" if consumed else "",
            ),
        )

    return CassetteDiff(
        old_path="",
        new_path="",
        matcher_names=matcher_names,
        pairs=pairs,
        removed=removed,
        added=added,
    )


def compare_cassettes(old_path, new_path, match_on=None, serializer=None):
    """Compare two cassette files and return a CassetteDiff report.

    ``match_on`` is a list of matcher names (as in ``VCR(match_on=...)``) or
    matcher callables; it defaults to VCR's default matchers. ``serializer``
    is "yaml" or "json"; by default it's guessed from each file's extension.
    """
    old_interactions = _load_interactions(old_path, serializer)
    new_interactions = _load_interactions(new_path, serializer)
    diff = compare_interactions(old_interactions, new_interactions, match_on)
    diff.old_path = str(old_path)
    diff.new_path = str(new_path)
    return diff


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python3 -m vcr.diff",
        description=(
            "Compare two cassette files offline: which interactions were added, "
            "removed, or had their response changed. Requests are matched with "
            "VCR's own matchers, so serialization noise doesn't count. "
            "Neither file is modified."
        ),
    )
    parser.add_argument("old_cassette", help="path to the old cassette file")
    parser.add_argument("new_cassette", help="path to the new (re-recorded) cassette file")
    parser.add_argument(
        "--match-on",
        nargs="+",
        default=None,
        metavar="MATCHER",
        help="request matchers to pair interactions with, e.g. method uri. "
        "Defaults to VCR's default: method scheme host port path query",
    )
    parser.add_argument(
        "--serializer",
        choices=sorted(_SERIALIZERS),
        default=None,
        help="cassette serializer for both files (default: guessed from the file extension)",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    try:
        diff = compare_cassettes(
            args.old_cassette,
            args.new_cassette,
            match_on=args.match_on,
            serializer=args.serializer,
        )
    except (KeyError, ValueError, OSError) as e:
        sys.stderr.write(f"Error: {e}\n")
        return 2

    if args.json:
        print(json.dumps(diff.to_dict(), indent=2))
    else:
        print(diff.render())
    return 0 if diff.identical else 1


if __name__ == "__main__":
    sys.exit(main())
