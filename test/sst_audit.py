#!/usr/bin/env python3
"""Read-only audit of shared-string table (SST) references in .xlsx packages.

Opens each package read-only and maps every t="s" cell back to the shared
string index it points at, then classifies the reference:

  ok                  index resolves to a non-empty string
  empty_string        index resolves to a real empty <si/> string
  index_out_of_range  index is negative or >= number of <si/> entries
  invalid_index       <v> is not an integer
  sst_missing         the package contains no sharedStrings part
  sheet_missing       a worksheet referenced by the workbook is absent

The current converter (xlsx2csv.py) collapses index_out_of_range,
invalid_index and sst_missing into an empty output cell, which is
indistinguishable from empty_string in the CSV. This tool keeps them apart.

Nothing is written or converted; the zip is opened with mode "r".
"""

import argparse
import json
import os
import sys
import zipfile
import xml.etree.ElementTree as ET

SST_REL_SUFFIX = "/sharedStrings"
WORKSHEET_REL_SUFFIX = "/worksheet"


def localname(tag):
    return tag.rsplit("}", 1)[-1]


def resolve_target(base_dir, target):
    if target.startswith("/"):
        return target.lstrip("/")
    return (base_dir + target).replace("/./", "/")


def find_part(zf, candidates):
    names = set(zf.namelist())
    for candidate in candidates:
        if candidate in names:
            return candidate
    return None


def parse_relationships(zf, rels_path):
    relationships = {}
    if not rels_path or rels_path not in zf.namelist():
        return relationships
    root = ET.fromstring(zf.read(rels_path))
    for rel in root:
        if localname(rel.tag) != "Relationship":
            continue
        rel_id = rel.get("Id")
        rel_type = rel.get("Type", "")
        target = rel.get("Target", "")
        relationships[rel_id] = {"type": rel_type, "target": target}
    return relationships


def parse_workbook_sheets(zf):
    root = ET.fromstring(zf.read("xl/workbook.xml"))
    sheets = []
    for node in root.iter():
        if localname(node.tag) != "sheet":
            continue
        sheets.append({
            "name": node.get("name"),
            "rid": node.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"),
        })
    return sheets


def parse_shared_strings(zf, sst_path):
    strings = []
    root = ET.fromstring(zf.read(sst_path))
    for node in root:
        if localname(node.tag) != "si":
            continue
        text = "".join(t.text or "" for t in node.iter() if localname(t.tag) == "t")
        strings.append(text)
    return strings


def parse_sheet_refs(zf, sheet_path):
    refs = []
    root = ET.fromstring(zf.read(sheet_path))
    for node in root.iter():
        if localname(node.tag) != "c" or node.get("t") != "s":
            continue
        value = None
        for child in node:
            if localname(child.tag) == "v":
                value = child.text
                break
        refs.append({"ref": node.get("r"), "raw_index": value})
    return refs


def classify(raw_index, strings, sst_path):
    if sst_path is None:
        return "sst_missing", None
    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        return "invalid_index", None
    if index < 0 or index >= len(strings):
        return "index_out_of_range", None
    if strings[index] == "":
        return "empty_string", ""
    return "ok", strings[index]


def audit(xlsx_path):
    report = {"file": xlsx_path, "sst_path": None, "sst_entries": 0, "cells": [], "issues": []}
    with zipfile.ZipFile(xlsx_path, "r") as zf:
        rels = parse_relationships(zf, "xl/_rels/workbook.xml.rels")

        sst_candidates = []
        for rel in rels.values():
            if rel["type"].endswith(SST_REL_SUFFIX):
                sst_candidates.append(resolve_target("xl/", rel["target"]))
        sst_candidates.append("xl/sharedStrings.xml")
        sst_path = find_part(zf, sst_candidates)
        report["sst_path"] = sst_path

        strings = parse_shared_strings(zf, sst_path) if sst_path else []
        report["sst_entries"] = len(strings)

        for sheet in parse_workbook_sheets(zf):
            rel = rels.get(sheet["rid"])
            sheet_target = resolve_target("xl/", rel["target"]) if rel else None
            if not sheet_target or sheet_target not in zf.namelist():
                issue = {
                    "sheet": sheet["name"], "cell": None, "index": None,
                    "status": "sheet_missing", "value": None,
                }
                report["cells"].append(issue)
                report["issues"].append(issue)
                continue

            for position, cell in enumerate(parse_sheet_refs(zf, sheet_target), start=1):
                status, value = classify(cell["raw_index"], strings, sst_path)
                entry = {
                    "sheet": sheet["name"],
                    "cell": cell["ref"] or "(no r attr)#%d" % position,
                    "index": cell["raw_index"],
                    "status": status,
                    "value": value,
                }
                report["cells"].append(entry)
                if status != "ok":
                    report["issues"].append(entry)
    return report


def print_text(report):
    print("file: %s" % report["file"])
    if report["sst_path"]:
        print("sst:  %s (%d entries)" % (report["sst_path"], report["sst_entries"]))
    else:
        print("sst:  MISSING (no sharedStrings part in package)")
    if not report["cells"]:
        print("  no t=\"s\" cells found")
    for cell in report["cells"]:
        preview = "" if cell["value"] is None else cell["value"]
        if len(preview) > 40:
            preview = preview[:37] + "..."
        print("  %-12s %-14s idx=%-4s %s" % (
            cell["sheet"], cell["cell"], str(cell["index"]), cell["status"]))
        if cell["status"] == "ok":
            print("               -> %r" % preview)
    if report["issues"]:
        print("issues: %d" % len(report["issues"]))
    print("")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("xlsx", nargs="+", help="xlsx/xlsm package(s) to audit")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    reports = []
    failed = False
    for path in args.xlsx:
        try:
            report = audit(path)
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            print("cannot audit %s: %s" % (path, exc), file=sys.stderr)
            return 2
        reports.append(report)
        if report["issues"]:
            failed = True

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        for report in reports:
            print_text(report)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
