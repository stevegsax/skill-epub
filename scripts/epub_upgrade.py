#!/usr/bin/env python3
"""Upgrade an EPUB 2 file to EPUB 3 while keeping its content files intact.

Usage:
    uv run --no-project epub_upgrade.py <in.epub> --output <out.epub> [--json]

Unlike a pandoc conversion, which rebuilds the book from pandoc's document
model and loses styling, cover markup, and file names, this script keeps
every content file and makes only the changes EPUB 3 requires:

  package document
    - version="3.0"
    - EPUB 2 attributes on Dublin Core elements (opf:role, opf:file-as,
      opf:scheme, opf:event) become EPUB 3 <meta refines> elements
    - empty Dublin Core elements are removed (EPUB 3 forbids them)
    - a dcterms:modified timestamp is added (now, UTC)
    - the cover image item gets properties="cover-image"
    - a navigation document generated from the NCX (and the guide, as
      landmarks) is added to the manifest with properties="nav"
  content documents (application/xhtml+xml)
    - the XHTML 1.1 DOCTYPE becomes <!DOCTYPE html>
    - HTML named character references that XML does not define
      (&nbsp; and the like) become numeric references

Everything else, including the NCX, the guide, CSS, images, and the
spine, is left as it was. Run epubcheck on the result: it reports content
problems (deprecated attributes, invalid markup) that only a human can
decide how to fix. Dependencies (lxml) are declared below (PEP 723).
"""

# /// script
# requires-python = ">=3.10"
# dependencies = ["lxml>=5"]
# ///

from __future__ import annotations

import argparse
import html.entities
import json
import os
import posixpath
import re
import sys
import zipfile

import epub_opf as opf
from epub_common import (
    DC_NS,
    EpubError,
    check_output_path,
    find_opf_path,
    is_iso_date,
    now_utc_iso,
    opf_dir,
    write_epub,
)
from lxml import etree

DOCTYPE_RE = re.compile(r"<!DOCTYPE[^\[>]*(?:\[.*?\])?\s*>", re.DOTALL | re.IGNORECASE)
XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
ENTITY_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")


def fix_content_document(data: bytes) -> tuple[bytes, list[str]]:
    """Apply the text-level changes EPUB 3 needs in an XHTML document."""
    notes: list[str] = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data, ["not UTF-8; left untouched"]

    new_text, n = DOCTYPE_RE.subn("<!DOCTYPE html>", text, count=1)
    if n and new_text != text:
        notes.append("DOCTYPE replaced with <!DOCTYPE html>")
    text = new_text

    replaced: set[str] = set()

    def entity(m):
        name = m.group(1)
        if name in XML_ENTITIES:
            return m.group(0)
        cp = html.entities.name2codepoint.get(name)
        if cp is None:
            return m.group(0)
        replaced.add(name)
        return f"&#{cp};"

    text = ENTITY_RE.sub(entity, text)
    if replaced:
        notes.append(f"named entities replaced: {', '.join(sorted(replaced))}")
    return text.encode("utf-8"), notes


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Upgrade an EPUB 2 file to EPUB 3 in place of its structure."
    )
    p.add_argument("epub", help="Path to the EPUB 2 file")
    p.add_argument("--output", "-o", required=True, help="Where to write the EPUB 3 file")
    p.add_argument("--json", action="store_true", help="Report as JSON")
    return p.parse_args(argv)


def run(args) -> dict:
    report: dict = {
        "input": args.epub,
        "output": args.output,
        "upgraded": False,
        "changes": [],
        "warnings": [],
        "notes": [],
    }
    changes, warnings, notes = report["changes"], report["warnings"], report["notes"]

    if not os.path.isfile(args.epub):
        raise EpubError(f"file not found: {args.epub}")
    check_output_path(args.epub, args.output)
    try:
        zf = zipfile.ZipFile(args.epub, "r")
    except zipfile.BadZipFile:
        raise EpubError(f"not a ZIP/EPUB file: {args.epub}") from None

    with zf:
        names = set(zf.namelist())
        opf_path = find_opf_path(zf)
        base = opf_dir(opf_path)
        root = opf.load_opf(zf.read(opf_path))
        version = opf.opf_version(root)
        if version.startswith("3"):
            notes.append(f"{args.epub} is already EPUB {version}; nothing to do")
            return report
        if not version.startswith("2"):
            raise EpubError(
                f"unexpected package version {version!r}; only EPUB 2 files are upgraded"
            )

        files_to_add: dict[str, bytes] = {}
        meta = opf.metadata_el(root)

        # --- package metadata ---------------------------------------------
        root.set("version", "3.0")
        changes.append("package version set to 3.0")
        changes += opf.drop_empty_dc(root)
        changes += opf.convert_opf2_attributes(root)

        uid = root.get("unique-identifier")
        ids = [el for el in meta.findall(opf.qn(DC_NS, "identifier"))]
        if not ids:
            raise EpubError("the package has no dc:identifier; add one before upgrading")
        if not uid or not any(el.get("id") == uid for el in ids):
            first = ids[0]
            if not first.get("id"):
                first.set("id", opf.unique_id(root, "pub-id"))
            root.set("unique-identifier", first.get("id"))
            changes.append(f"unique-identifier set to {first.get('id')}")

        for el in meta.findall(opf.qn(DC_NS, "date")):
            if not is_iso_date((el.text or "").strip()):
                warnings.append(
                    f"dc:date value {el.text.strip()!r} is not an ISO 8601 date; "
                    "set one with epub_update.py --date"
                )

        titles = meta.findall(opf.qn(DC_NS, "title"))
        title = (titles[0].text or "").strip() if titles else "Untitled"
        langs = meta.findall(opf.qn(DC_NS, "language"))
        lang = (langs[0].text or "").strip() if langs else None

        cover = opf.find_cover_item(root)
        if cover is not None:
            changes += opf.set_cover_marker(root, cover)

        # --- navigation document ------------------------------------------
        if opf.find_nav_item(root) is None:
            nav_path = posixpath.join(base, "nav.xhtml") if base else "nav.xhtml"
            if nav_path in names:
                nav_path = posixpath.join(base, "epub3-nav.xhtml") if base else "epub3-nav.xhtml"
            data, nav_warnings = opf.build_nav_document(root, base, zf, nav_path, title, lang)
            warnings += nav_warnings
            files_to_add[nav_path] = data
            opf.add_manifest_item(root, base, nav_path, "application/xhtml+xml", "nav", "nav")
            changes.append(
                f"navigation document {nav_path} generated from the NCX and added to the manifest"
            )
        else:
            notes.append("a navigation document is already present; not regenerated")

        # --- content documents --------------------------------------------
        for item in opf.manifest_el(root).findall(opf.OPF_ITEM):
            if item.get("media-type") != "application/xhtml+xml":
                continue
            path = opf.item_path(root, base, item)
            if path in files_to_add:
                continue  # generated in this run (the nav document)
            if path not in names:
                warnings.append(
                    f"manifest item {item.get('id')} points to {path}, which is missing"
                )
                continue
            new_data, doc_notes = fix_content_document(zf.read(path))
            if doc_notes and new_data != zf.read(path):
                files_to_add[path] = new_data
                changes.append(f"{path}: {'; '.join(doc_notes)}")
            elif doc_notes:
                warnings.append(f"{path}: {'; '.join(doc_notes)}")

        changes += opf.set_modified(root, now_utc_iso())
        files_to_add[opf_path] = opf.dump_opf(root)
        write_epub(args.epub, args.output, files_to_add, set())
        report["upgraded"] = True
        notes.append("Run epubcheck on the output; content-level problems are reported there.")
    return report


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        report = run(args)
    except (EpubError, etree.XMLSyntaxError) as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if report["changes"]:
        print("Changes:")
        for c in report["changes"]:
            print(f"  - {c}")
    for w in report["warnings"]:
        print(f"Warning: {w}")
    for n in report["notes"]:
        print(f"Note: {n}")
    if report["upgraded"]:
        print(f"\nWritten to: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
