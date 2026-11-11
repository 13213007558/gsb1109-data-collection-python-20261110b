# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import sys

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from wilderness import Command

from clevercsv.wrappers import detect_multipart_dialect

from ._docs import FLAG_DESCRIPTIONS


class SegmentsCommand(Command):
    _description = (
        "Detect contiguous segments with different dialects in a CSV "
        "file. This is useful when a file concatenates exports from "
        "different systems, for example a first part delimited by commas "
        "followed by a part delimited by semicolons. The command reports "
        "the record and character offsets of each segment together with "
        "the dialect detected for each segment, so that each part can be "
        "read separately. Boundaries are only reported between complete "
        "records; records with embedded newlines are never split. When no "
        "reliable boundary can be established, the command exits with "
        "status 3 and leaves the file for manual review; the regular "
        "'detect' command remains available for single-dialect files."
    )

    def __init__(self) -> None:
        super().__init__(
            name="segments",
            title="Detect contiguous dialect segments in a CSV file",
            description=self._description,
            extra_sections={"CleverCSV": "Part of the CleverCSV suite"},
        )

    def register(self) -> None:
        self.add_argument("path", help="Path to the CSV file")
        self.add_argument(
            "-e",
            "--encoding",
            help="Set the encoding of the file",
            description=FLAG_DESCRIPTIONS["encoding"],
        )
        self.add_argument(
            "-d",
            "--delimiters",
            help=(
                "Delimiter characters to consider for the two dialects, "
                "as a single string (default: ',;')."
            ),
        )
        self.add_argument(
            "--min-support",
            type=int,
            help=(
                "Minimum number of records that must clearly fit each "
                "dialect (default: 3)."
            ),
        )
        self.add_argument(
            "--tolerance",
            type=float,
            help=(
                "Maximum fraction of records on either side of the "
                "boundary that may fail to fit the dialect of that side, "
                "for instance rows missing a column (default: 0.1)."
            ),
        )
        self.add_argument(
            "-j",
            "--json",
            action="store_true",
            help="Print the segments as a JSON array",
            description=(
                "Print the segments to standard output as a JSON array. "
                "Each object has 'start_row', 'end_row', 'start_offset', "
                "'end_offset', and a 'dialect' object with the "
                "'delimiter', 'quotechar', 'escapechar', and 'strict' "
                "keys."
            ),
        )

    def handle(self) -> int:
        delimiters: Optional[List[str]] = None
        if self.args.delimiters is not None:
            delimiters = list(self.args.delimiters)
            if not delimiters:
                print(
                    "Error: at least one delimiter character is required.",
                    file=sys.stderr,
                )
                return 1

        if self.args.min_support is not None and self.args.min_support < 1:
            print(
                "Error: --min-support should be a positive integer.",
                file=sys.stderr,
            )
            return 1
        if self.args.tolerance is not None and not (
            0.0 <= self.args.tolerance < 1.0
        ):
            print(
                "Error: --tolerance should be between 0 and 1.",
                file=sys.stderr,
            )
            return 1

        try:
            segments = detect_multipart_dialect(
                self.args.path,
                encoding=self.args.encoding,
                delimiters=delimiters,
                min_support=self.args.min_support,
                tolerance=self.args.tolerance,
                verbose=self.args.verbose,
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        if segments is None:
            print(
                "No reliable dialect boundary could be established; "
                "please review the file manually.",
                file=sys.stderr,
            )
            return 3

        if self.args.json:
            payload = [
                {
                    "start_row": segment.start_row,
                    "end_row": segment.end_row,
                    "start_offset": segment.start_offset,
                    "end_offset": segment.end_offset,
                    "dialect": segment.dialect.to_dict(),
                }
                for segment in segments
            ]
            print(json.dumps(payload))
            return 0

        for index, segment in enumerate(segments, start=1):
            dialect: Dict[str, Any] = segment.dialect.to_dict()
            print(
                "Segment %d: rows %d-%d, offsets %d-%d, dialect: %r"
                % (
                    index,
                    segment.start_row,
                    segment.end_row - 1,
                    segment.start_offset,
                    segment.end_offset,
                    segment.dialect,
                )
            )
            print(
                "          delimiter=%r quotechar=%r escapechar=%r strict=%r"
                % (
                    dialect["delimiter"],
                    dialect["quotechar"],
                    dialect["escapechar"],
                    dialect["strict"],
                )
            )
        return 0
