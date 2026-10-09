#!/usr/bin/env python3
"""Modify EPUB metadata, cover image, or internal files, writing a new file.

Usage:
    uv run --no-project epub_update.py <in.epub> --output <out.epub> [options]

Metadata options (each sets the whole value of that field):
    --title TEXT              dc:title
    --author TEXT             dc:creator (repeatable, in order)
    --contributor TEXT        dc:contributor (repeatable)
    --subject TEXT            dc:subject (repeatable)
    --language TEXT           dc:language (BCP 47 tag, e.g. en-US)
    --description TEXT        dc:description
    --publisher TEXT          dc:publisher
    --date TEXT               dc:date (ISO 8601: 2026, 2026-01, or 2026-01-31)
    --identifier TEXT         dc:identifier named by unique-identifier
    --rights TEXT             dc:rights
    --set KEY=VALUE           any key, e.g. dc:source=... or
                              schema:accessibilitySummary=... (repeatable)
    --remove-meta KEY         remove every element for KEY (repeatable)
    --metadata-file FILE      JSON-LD from epub_metadata.py --json/--summary;
                              every key present is applied, keys absent are
                              left alone, an empty list removes a key
    --modified TIMESTAMP      dcterms:modified value; default: now, UTC

File options:
    --cover IMAGE             replace (or add) the cover image
    --add FILE:PATH           add a local file at an archive path; it is
                              listed in the manifest, and XHTML goes to the
                              end of the spine
    --remove PATH             remove an archive path, its manifest item,
                              spine entry, and navigation links

Other:
    --output, -o FILE         required unless --dry-run; never the input
    --dry-run                 report the changes without writing
    --json                    report as JSON on stdout

dcterms:modified is set to the current UTC time whenever an EPUB 3 file is
written, unless --modified gives a value. Dependencies (lxml) are declared
below (PEP 723), so `uv run` provisions them.
"""

# /// script
# requires-python = ">=3.10"
# dependencies = ["lxml>=5"]
# ///

from __future__ import annotations

import argparse
import json
import os
import posixpath
import sys
import zipfile

import epub_opf as opf
from epub_common import (
    EpubError,
    check_output_path,
    find_opf_path,
    guess_media_type,
    is_iso_date,
    is_modified_timestamp,
    now_utc_iso,
    opf_dir,
    relative_href,
    write_epub,
)
from lxml import etree

CLI_FIELDS = {
    "title": "dc:title",
    "author": "dc:creator",
    "contributor": "dc:contributor",
    "subject": "dc:subject",
    "language": "dc:language",
    "description": "dc:description",
    "publisher": "dc:publisher",
    "date": "dc:date",
    "identifier": "dc:identifier",
    "rights": "dc:rights",
}


def load_metadata_file(path: str) -> dict:
    """Read a JSON-LD metadata object (or a full epub_metadata.py JSON dump)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    meta = data.get("metadata", data) if isinstance(data, dict) else None
    if not isinstance(meta, dict):
        raise EpubError(f"{path}: expected a JSON object with metadata keys")
    return {k: v for k, v in meta.items() if not k.startswith("@")}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Modify EPUB metadata, cover image, or files.")
    p.add_argument("epub", help="Path to the input EPUB file")
    p.add_argument("--output", "-o", help="Output path (required unless --dry-run)")
    p.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    p.add_argument("--json", action="store_true", help="Report as JSON")

    m = p.add_argument_group("metadata")
    m.add_argument("--title")
    m.add_argument("--author", action="append")
    m.add_argument("--contributor", action="append")
    m.add_argument("--subject", action="append")
    m.add_argument("--language")
    m.add_argument("--description")
    m.add_argument("--publisher")
    m.add_argument("--date")
    m.add_argument("--identifier")
    m.add_argument("--rights")
    m.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help="Set any metadata key, e.g. schema:accessibilitySummary=...",
    )
    m.add_argument(
        "--remove-meta",
        action="append",
        metavar="KEY",
        help="Remove every element for a metadata key, e.g. dc:subject",
    )
    m.add_argument("--metadata-file", help="JSON-LD metadata to apply")
    m.add_argument("--modified", help="dcterms:modified value (default: now, UTC)")

    f = p.add_argument_group("files")
    f.add_argument("--cover", help="Replace or add the cover image")
    f.add_argument(
        "--add", action="append", metavar="FILE:PATH", help="Add a local file at an archive path"
    )
    f.add_argument(
        "--remove",
        action="append",
        metavar="PATH",
        help="Remove an archive path and the package entries for it",
    )
    args = p.parse_args(argv)
    if not args.dry_run and not args.output:
        p.error("--output is required (the input file is never modified in place)")
    return args


def collect_metadata_updates(args) -> dict:
    """Merge --metadata-file with CLI flags; flags win. Returns key → value(s)."""
    updates: dict = {}
    if args.metadata_file:
        updates.update(load_metadata_file(args.metadata_file))
        # dcterms:modified from the file would preserve a stale timestamp.
        updates.pop("dcterms:modified", None)
    for flag, key in CLI_FIELDS.items():
        val = getattr(args, flag)
        if val is not None:
            updates[key] = val
    for spec in args.set or []:
        if "=" not in spec:
            raise EpubError(f"--set needs KEY=VALUE, got {spec!r}")
        key, value = spec.split("=", 1)
        key = key.strip()
        if key in updates and isinstance(updates[key], list):
            updates[key].append(value)
        elif key in updates:
            updates[key] = [updates[key], value]
        else:
            updates[key] = value
    for key in args.remove_meta or []:
        updates[key] = []
    if "dc:date" in updates:
        for v in (
            updates["dc:date"] if isinstance(updates["dc:date"], list) else [updates["dc:date"]]
        ):
            if not is_iso_date(opf.node_text(v)):
                raise EpubError(f"--date must be ISO 8601 (2026, 2026-01, 2026-01-31), got {v!r}")
    return updates


def run(args) -> dict:
    report: dict = {
        "input": args.epub,
        "output": args.output,
        "changes": [],
        "warnings": [],
        "notes": [],
    }
    changes, warnings, notes = report["changes"], report["warnings"], report["notes"]

    if not os.path.isfile(args.epub):
        raise EpubError(f"file not found: {args.epub}")
    if args.output:
        check_output_path(args.epub, args.output)
    if args.modified and not is_modified_timestamp(args.modified):
        raise EpubError(f"--modified must look like 2026-01-31T12:00:00Z, got {args.modified!r}")

    try:
        zf = zipfile.ZipFile(args.epub, "r")
    except zipfile.BadZipFile:
        raise EpubError(f"not a ZIP/EPUB file: {args.epub}") from None

    with zf:
        names = set(zf.namelist())
        opf_path = find_opf_path(zf)
        base = opf_dir(opf_path)
        try:
            root = opf.load_opf(zf.read(opf_path))
        except EpubError as e:
            raise EpubError(f"{opf_path}: {e}") from None
        files_to_add: dict[str, bytes] = {}
        files_to_remove: set[str] = set()

        # --- metadata ------------------------------------------------------
        updates = collect_metadata_updates(args)
        if updates:
            changes += opf.apply_metadata(root, updates)

        # --- cover ---------------------------------------------------------
        if args.cover:
            if not os.path.isfile(args.cover):
                raise EpubError(f"cover file not found: {args.cover}")
            media_type = guess_media_type(args.cover)
            if not media_type or not media_type.startswith("image/"):
                raise EpubError(f"cover does not look like an image: {args.cover}")
            with open(args.cover, "rb") as fh:
                data = fh.read()
            ext = posixpath.splitext(args.cover)[1].lower()
            item = opf.find_cover_item(root)
            if item is not None:
                old_path = opf.item_path(root, base, item)
                new_path = old_path
                if posixpath.splitext(old_path)[1].lower() != ext:
                    new_path = posixpath.splitext(old_path)[0] + ext
                    if new_path in names:
                        raise EpubError(f"cannot rename cover to {new_path}: that file exists")
                    item.set("href", relative_href(base, new_path))
                    files_to_remove.add(old_path)
                    rewritten = opf.rewrite_references(root, base, zf, old_path, new_path)
                    files_to_add.update(rewritten)
                    for doc in rewritten:
                        changes.append(f"Updated references to the cover in {doc}")
                    changes.append(f"Renamed cover {old_path} -> {new_path}")
                item.set("media-type", media_type)
                files_to_add[new_path] = data
                changes.append(f"Replaced cover image {new_path} ({media_type})")
            else:
                new_path = (
                    posixpath.join(base, f"images/cover{ext}") if base else f"images/cover{ext}"
                )
                if new_path in names:
                    raise EpubError(f"cannot add cover at {new_path}: that file exists")
                item = opf.add_manifest_item(root, base, new_path, media_type, "cover-image")
                files_to_add[new_path] = data
                changes.append(f"Added cover image {new_path} ({media_type})")
            changes += opf.set_cover_marker(root, item)
            notes.append("A cover image only is set; no cover XHTML page was created or changed.")

        # --- add files -----------------------------------------------------
        for spec in args.add or []:
            if ":" not in spec:
                raise EpubError(f"--add needs FILE:PATH, got {spec!r}")
            local, path = spec.rsplit(":", 1)
            if not os.path.isfile(local):
                raise EpubError(f"file not found: {local}")
            path = posixpath.normpath(path)
            if path in names or path in files_to_add:
                raise EpubError(
                    f"{path} already exists in the EPUB; use --remove first to replace it"
                )
            media_type = guess_media_type(path)
            if not media_type:
                raise EpubError(
                    f"cannot determine a media type for {path}; use a standard extension"
                )
            with open(local, "rb") as fh:
                files_to_add[path] = fh.read()
            item = opf.add_manifest_item(root, base, path, media_type)
            changes.append(f"Added {path} as manifest item {item.get('id')} ({media_type})")
            if media_type == "application/xhtml+xml":
                opf.add_spine_item(root, item.get("id"))
                changes.append(f"Appended {item.get('id')} to the end of the spine")
                notes.append(
                    f"{path} is last in the reading order and is not in the table of contents."
                )

        # --- remove files --------------------------------------------------
        for path in args.remove or []:
            path = posixpath.normpath(path)
            if path not in names:
                raise EpubError(f"{path} is not in the EPUB")
            if path in ("mimetype", "META-INF/container.xml", opf_path):
                raise EpubError(f"refusing to remove {path}: the EPUB needs it")
            item = opf.find_item_by_path(root, base, path)
            if item is not None:
                if item is opf.find_nav_item(root):
                    raise EpubError("refusing to remove the navigation document")
                changes += opf.remove_manifest_item(root, item)
            else:
                warnings.append(f"{path} was not listed in the manifest")
            files_to_remove.add(path)
            changes.append(f"Removed {path}")

            nav_item = opf.find_nav_item(root)
            if nav_item is not None:
                nav_path = opf.item_path(root, base, nav_item)
                if nav_path in names:
                    src = files_to_add.get(nav_path) or zf.read(nav_path)
                    new, n = opf.remove_nav_links(src, posixpath.dirname(nav_path), path)
                    if n:
                        files_to_add[nav_path] = new
                        changes.append(f"Removed {n} link(s) to {path} from {nav_path}")
            ncx_item = opf.find_ncx_item(root)
            if ncx_item is not None:
                ncx_path = opf.item_path(root, base, ncx_item)
                if ncx_path in names:
                    src = files_to_add.get(ncx_path) or zf.read(ncx_path)
                    new, n = opf.remove_ncx_points(src, posixpath.dirname(ncx_path), path)
                    if n:
                        files_to_add[ncx_path] = new
                        changes.append(f"Removed {n} navPoint(s) for {path} from {ncx_path}")
            for doc, href in opf.find_references(
                root, base, zf, path, exclude=files_to_remove, overlay=files_to_add
            ):
                warnings.append(f"{doc} still references {path} as {href!r}; edit it by hand")

        if not changes:
            raise EpubError("no changes requested; run with --help for the options")

        if opf.is_epub3(root):
            stamp = args.modified or now_utc_iso()
            changes += opf.set_modified(root, stamp)
        elif args.modified:
            warnings.append("--modified ignored: EPUB 2 packages have no dcterms:modified")
        else:
            notes.append("EPUB 2 package: no dcterms:modified to update; consider epub_upgrade.py")

        files_to_add[opf_path] = opf.dump_opf(root)
        if not args.dry_run:
            write_epub(args.epub, args.output, files_to_add, files_to_remove)
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
    report["dry_run"] = args.dry_run
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print("Changes:")
    for c in report["changes"]:
        print(f"  - {c}")
    for w in report["warnings"]:
        print(f"Warning: {w}")
    for n in report["notes"]:
        print(f"Note: {n}")
    if args.dry_run:
        print("\n(dry run: nothing written)")
    else:
        print(f"\nWritten to: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
