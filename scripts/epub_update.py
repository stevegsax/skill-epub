#!/usr/bin/env python3
"""Modify EPUB metadata, cover image, or internal files.

Usage:
    python3 epub_update.py <file.epub> [options]

Options:
    --title TEXT          Set the book title
    --author TEXT         Set the book author (repeatable)
    --language TEXT       Set the language code (e.g., en-US)
    --description TEXT    Set the book description
    --publisher TEXT      Set the publisher
    --date TEXT           Set the publication date
    --identifier TEXT     Set the unique identifier
    --rights TEXT         Set the rights statement
    --metadata-file FILE Load metadata from a JSON-LD file (as produced by epub_metadata.py --json)
    --cover IMAGE        Replace the cover image
    --add FILE:PATH      Add a file to the EPUB at the given internal path
    --remove PATH        Remove a file from the EPUB by internal path
    --output FILE        Output path (default: overwrites input)
    --dry-run            Show what would change without modifying the file

No external dependencies — uses only the Python 3 standard library.
"""

import argparse
import json
import mimetypes
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import PurePosixPath


NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# Register namespaces so they're preserved in output
for prefix, uri in NS.items():
    ET.register_namespace(prefix if prefix != "opf" else "", uri)
ET.register_namespace("dcterms", "http://purl.org/dc/terms/")


def find_opf_path(zf: zipfile.ZipFile) -> str:
    """Locate the OPF file path from META-INF/container.xml."""
    container_xml = zf.read("META-INF/container.xml")
    root = ET.fromstring(container_xml)
    rootfile = root.find(".//container:rootfile", NS)
    if rootfile is None:
        raise ValueError("No rootfile found in container.xml")
    return rootfile.attrib["full-path"]


def set_dc_element(metadata: ET.Element, tag: str, values: list[str]):
    """Set a Dublin Core metadata element, replacing any existing ones."""
    full_tag = f"{{{NS['dc']}}}{tag}"

    # Remove existing elements with this tag
    for existing in metadata.findall(f"dc:{tag}", NS):
        metadata.remove(existing)

    # Add new elements
    for value in values:
        el = ET.SubElement(metadata, full_tag)
        el.text = value


def load_metadata_file(path: str) -> dict:
    """Load metadata from a JSON-LD file produced by epub_metadata.py.

    Returns a dict with CLI-compatible field names and optionally a
    'dcterms:modified' key for the OPF meta element.
    """
    with open(path) as f:
        data = json.load(f)

    meta = data.get("metadata", data)

    dc_to_cli = {
        "dc:title": "title",
        "dc:creator": "author",
        "dc:language": "language",
        "dc:description": "description",
        "dc:publisher": "publisher",
        "dc:date": "date",
        "dc:identifier": "identifier",
        "dc:rights": "rights",
    }

    result = {}
    for dc_key, cli_key in dc_to_cli.items():
        if dc_key in meta:
            result[cli_key] = meta[dc_key]

    if "dcterms:modified" in meta:
        result["dcterms:modified"] = meta["dcterms:modified"]

    return result


def update_metadata(opf_root: ET.Element, updates: dict) -> list[str]:
    """Apply metadata updates to the OPF document. Returns list of changes made."""
    metadata = opf_root.find("opf:metadata", NS)
    if metadata is None:
        raise ValueError("No metadata element found in OPF")

    changes = []
    dc_field_map = {
        "title": "title",
        "author": "creator",
        "language": "language",
        "description": "description",
        "publisher": "publisher",
        "date": "date",
        "identifier": "identifier",
        "rights": "rights",
    }

    for key, dc_tag in dc_field_map.items():
        if key in updates and updates[key] is not None:
            values = updates[key] if isinstance(updates[key], list) else [updates[key]]
            set_dc_element(metadata, dc_tag, values)
            changes.append(f"Set {key}: {', '.join(values)}")

    # Handle dcterms:modified meta element
    if "dcterms:modified" in updates:
        modified_value = updates["dcterms:modified"]
        found = False
        for meta in metadata.findall("opf:meta", NS):
            if meta.get("property") == "dcterms:modified":
                meta.text = modified_value
                found = True
                break
        if not found:
            meta_el = ET.SubElement(metadata, f"{{{NS['opf']}}}meta")
            meta_el.set("property", "dcterms:modified")
            meta_el.text = modified_value
        changes.append(f"Set dcterms:modified: {modified_value}")

    return changes


def find_cover_item(opf_root: ET.Element) -> tuple[str | None, str | None]:
    """Find the cover image item ID and href in the manifest."""
    metadata = opf_root.find("opf:metadata", NS)
    manifest = opf_root.find("opf:manifest", NS)
    if metadata is None or manifest is None:
        return None, None

    # Check for meta name="cover" content="item-id" (EPUB 2)
    for meta in metadata.findall("opf:meta", NS):
        if meta.get("name") == "cover":
            cover_id = meta.get("content")
            if cover_id:
                for item in manifest.findall("opf:item", NS):
                    if item.get("id") == cover_id:
                        return cover_id, item.get("href")

    # Check for item with properties="cover-image" (EPUB 3)
    for item in manifest.findall("opf:item", NS):
        if "cover-image" in item.get("properties", ""):
            return item.get("id"), item.get("href")

    return None, None


def replace_cover(opf_root: ET.Element, opf_path: str,
                  cover_path: str) -> tuple[list[str], dict[str, bytes]]:
    """Replace the cover image. Returns changes list and files to add."""
    changes = []
    files_to_add = {}

    cover_id, cover_href = find_cover_item(opf_root)
    opf_dir = str(PurePosixPath(opf_path).parent)

    # Determine the media type of the new cover
    mime_type, _ = mimetypes.guess_type(cover_path)
    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError(f"Cover file does not appear to be an image: {cover_path}")

    with open(cover_path, "rb") as f:
        cover_data = f.read()

    if cover_href:
        # Replace existing cover
        full_cover_path = str(PurePosixPath(opf_dir) / cover_href) if opf_dir != "." else cover_href
        files_to_add[full_cover_path] = cover_data

        # Update media-type if it changed
        manifest = opf_root.find("opf:manifest", NS)
        if manifest is None:
            raise ValueError("No manifest element found in OPF")
        for item in manifest.findall("opf:item", NS):
            if item.get("id") == cover_id:
                item.set("media-type", mime_type)
        changes.append(f"Replaced cover image: {full_cover_path}")
    else:
        # Add new cover image
        ext = os.path.splitext(cover_path)[1]
        new_href = f"images/cover{ext}"
        full_new_path = str(PurePosixPath(opf_dir) / new_href) if opf_dir != "." else new_href
        files_to_add[full_new_path] = cover_data

        # Add to manifest
        manifest = opf_root.find("opf:manifest", NS)
        if manifest is None:
            raise ValueError("No manifest element found in OPF")
        new_item = ET.SubElement(manifest, f"{{{NS['opf']}}}item")
        new_item.set("id", "cover-image")
        new_item.set("href", new_href)
        new_item.set("media-type", mime_type)
        new_item.set("properties", "cover-image")

        # Add meta element for EPUB 2 compatibility
        metadata = opf_root.find("opf:metadata", NS)
        if metadata is None:
            raise ValueError("No metadata element found in OPF")
        meta = ET.SubElement(metadata, f"{{{NS['opf']}}}meta")
        meta.set("name", "cover")
        meta.set("content", "cover-image")

        changes.append(f"Added cover image: {full_new_path}")

    return changes, files_to_add


def repackage_epub(input_path: str, output_path: str, opf_path: str,
                   opf_root: ET.Element, files_to_add: dict[str, bytes],
                   files_to_remove: set[str]):
    """Repackage the EPUB with modifications."""
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        with zipfile.ZipFile(input_path, "r") as zf_in:
            with zipfile.ZipFile(tmp_path, "w") as zf_out:
                # Write mimetype first, uncompressed
                if "mimetype" in zf_in.namelist():
                    zf_out.writestr("mimetype", zf_in.read("mimetype"),
                                    compress_type=zipfile.ZIP_STORED)

                # Copy existing files (except those being replaced/removed)
                replaced_paths = set(files_to_add.keys()) | files_to_remove | {opf_path, "mimetype"}
                for item in zf_in.infolist():
                    if item.filename not in replaced_paths:
                        zf_out.writestr(item, zf_in.read(item.filename))

                # Write modified OPF
                opf_bytes = ET.tostring(opf_root, encoding="unicode", xml_declaration=True)
                zf_out.writestr(opf_path, opf_bytes)

                # Write new/replaced files
                for path, data in files_to_add.items():
                    zf_out.writestr(path, data)

        # Move temp file to output
        shutil.move(tmp_path, output_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def parse_args():
    parser = argparse.ArgumentParser(
        description="Modify EPUB metadata, cover image, or internal files.",
    )
    parser.add_argument("epub", help="Path to the EPUB file")
    parser.add_argument("--title", help="Set the book title")
    parser.add_argument("--author", action="append", help="Set author (repeatable)")
    parser.add_argument("--language", help="Set language code")
    parser.add_argument("--description", help="Set description")
    parser.add_argument("--publisher", help="Set publisher")
    parser.add_argument("--date", help="Set publication date")
    parser.add_argument("--identifier", help="Set unique identifier")
    parser.add_argument("--rights", help="Set rights statement")
    parser.add_argument("--metadata-file",
                        help="JSON-LD metadata file (as produced by epub_metadata.py --json)")
    parser.add_argument("--cover", help="Replace cover image with this file")
    parser.add_argument("--add", action="append", metavar="FILE:PATH",
                        help="Add a file at the given internal path")
    parser.add_argument("--remove", action="append", metavar="PATH",
                        help="Remove a file by internal path")
    parser.add_argument("--output", "-o", help="Output path (default: overwrites input)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show changes without modifying")
    return parser.parse_args()


def main():
    args = parse_args()
    epub_path = args.epub
    output_path = args.output or epub_path

    if not os.path.isfile(epub_path):
        print(f"Error: File not found: {epub_path}", file=sys.stderr)
        sys.exit(1)

    try:
        zf = zipfile.ZipFile(epub_path, "r")
    except zipfile.BadZipFile:
        print(f"Error: Not a valid ZIP/EPUB file: {epub_path}", file=sys.stderr)
        sys.exit(1)

    with zf:
        opf_path = find_opf_path(zf)
        opf_xml = zf.read(opf_path)
        opf_root = ET.fromstring(opf_xml)

        all_changes = []
        files_to_add = {}
        files_to_remove = set()

        # Load metadata from JSON-LD file if provided
        file_meta = {}
        if args.metadata_file:
            file_meta = load_metadata_file(args.metadata_file)

        # Metadata updates: file values first, CLI flags override
        meta_updates = dict(file_meta)
        for k in ["title", "author", "language", "description",
                   "publisher", "date", "identifier", "rights"]:
            cli_val = getattr(args, k)
            if cli_val is not None:
                meta_updates[k] = cli_val
        if meta_updates:
            all_changes.extend(update_metadata(opf_root, meta_updates))

        # Cover replacement
        if args.cover:
            cover_changes, cover_files = replace_cover(
                opf_root, opf_path, args.cover
            )
            all_changes.extend(cover_changes)
            files_to_add.update(cover_files)

        # File additions
        if args.add:
            for add_spec in args.add:
                if ":" not in add_spec:
                    print(f"Error: --add requires FILE:PATH format, got: {add_spec}",
                          file=sys.stderr)
                    sys.exit(1)
                local_file, internal_path = add_spec.split(":", 1)
                if not os.path.isfile(local_file):
                    print(f"Error: File not found: {local_file}", file=sys.stderr)
                    sys.exit(1)
                with open(local_file, "rb") as f:
                    files_to_add[internal_path] = f.read()
                all_changes.append(f"Added file: {internal_path}")

        # File removals
        if args.remove:
            for path in args.remove:
                files_to_remove.add(path)
                all_changes.append(f"Removed file: {path}")

        if not all_changes:
            print("No changes specified. Use --help for usage.", file=sys.stderr)
            sys.exit(1)

        # Report changes
        print("Changes:")
        for change in all_changes:
            print(f"  - {change}")

        if args.dry_run:
            print("\n(dry run — no files modified)")
            return

        # Apply changes
        repackage_epub(epub_path, output_path, opf_path, opf_root,
                       files_to_add, files_to_remove)
        print(f"\nWritten to: {output_path}")


if __name__ == "__main__":
    main()
