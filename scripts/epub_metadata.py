#!/usr/bin/env python3
"""Extract and display metadata, TOC, spine, and manifest from an EPUB file.

Usage:
    python3 epub_metadata.py <file.epub> [--json]

Outputs structured metadata to stdout. Use --json for machine-readable output.
No external dependencies — uses only the Python 3 standard library.
"""

import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath


# XML namespaces used in EPUB
NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
}


def find_opf_path(zf: zipfile.ZipFile) -> str:
    """Locate the OPF file path from META-INF/container.xml."""
    try:
        container_xml = zf.read("META-INF/container.xml")
    except KeyError:
        raise ValueError("Not a valid EPUB: missing META-INF/container.xml")

    root = ET.fromstring(container_xml)
    rootfile = root.find(".//container:rootfile", NS)
    if rootfile is None:
        raise ValueError("No rootfile found in container.xml")
    return rootfile.attrib["full-path"]


def parse_metadata(opf_root: ET.Element) -> dict:
    """Extract Dublin Core and OPF metadata from the package document as JSON-LD."""
    metadata_el = opf_root.find("opf:metadata", NS)
    if metadata_el is None:
        return {}

    result = {
        "@context": {
            "dc": "http://purl.org/dc/elements/1.1/",
            "dcterms": "http://purl.org/dc/terms/",
        },
        "@type": "dcterms:BibliographicResource",
    }

    # Dublin Core elements
    dc_fields = [
        "title", "creator", "subject", "description", "publisher",
        "contributor", "date", "type", "format", "identifier",
        "source", "language", "relation", "coverage", "rights",
    ]
    for field in dc_fields:
        elements = metadata_el.findall(f"dc:{field}", NS)
        if elements:
            values = [el.text.strip() for el in elements if el.text]
            if len(values) == 1:
                result[f"dc:{field}"] = values[0]
            elif values:
                result[f"dc:{field}"] = values

    # OPF meta elements — promote dc:/dcterms: to top-level, collect others in epubMeta
    epub_meta = []
    for meta in metadata_el.findall("opf:meta", NS):
        prop = meta.get("property", "")
        if prop.startswith("dc:") or prop.startswith("dcterms:"):
            value = meta.text.strip() if meta.text and meta.text.strip() else ""
            if value:
                result[prop] = value
        else:
            entry = dict(meta.attrib)
            if meta.text and meta.text.strip():
                entry["value"] = meta.text.strip()
            if entry:
                epub_meta.append(entry)
    if epub_meta:
        result["epubMeta"] = epub_meta

    return result


def parse_manifest(opf_root: ET.Element) -> list[dict]:
    """Extract the manifest (list of all resources)."""
    manifest_el = opf_root.find("opf:manifest", NS)
    if manifest_el is None:
        return []

    items = []
    for item in manifest_el.findall("opf:item", NS):
        entry = {
            "id": item.get("id", ""),
            "href": item.get("href", ""),
            "media-type": item.get("media-type", ""),
        }
        props = item.get("properties", "")
        if props:
            entry["properties"] = props
        items.append(entry)
    return items


def parse_spine(opf_root: ET.Element) -> list[dict]:
    """Extract the spine (reading order)."""
    spine_el = opf_root.find("opf:spine", NS)
    if spine_el is None:
        return []

    items = []
    for itemref in spine_el.findall("opf:itemref", NS):
        entry = {"idref": itemref.get("idref", "")}
        linear = itemref.get("linear")
        if linear:
            entry["linear"] = linear
        items.append(entry)
    return items


def parse_toc_ncx(zf: zipfile.ZipFile, ncx_path: str) -> list[dict]:
    """Parse an NCX table of contents (EPUB 2 style)."""
    try:
        ncx_xml = zf.read(ncx_path)
    except KeyError:
        return []

    root = ET.fromstring(ncx_xml)
    nav_map = root.find("ncx:navMap", NS)
    if nav_map is None:
        return []

    def parse_navpoints(parent):
        entries = []
        for np in parent.findall("ncx:navPoint", NS):
            label_el = np.find("ncx:navLabel/ncx:text", NS)
            content_el = np.find("ncx:content", NS)
            entry = {
                "label": label_el.text.strip() if label_el is not None and label_el.text else "",
                "src": content_el.get("src", "") if content_el is not None else "",
            }
            children = parse_navpoints(np)
            if children:
                entry["children"] = children
            entries.append(entry)
        return entries

    return parse_navpoints(nav_map)


def parse_toc_nav(zf: zipfile.ZipFile, nav_path: str) -> list[dict]:
    """Parse an XHTML navigation document (EPUB 3 style)."""
    try:
        nav_xml = zf.read(nav_path)
    except KeyError:
        return []

    root = ET.fromstring(nav_xml)

    # Find the nav element with epub:type="toc"
    for nav in root.iter(f"{{{NS['xhtml']}}}nav"):
        if "toc" in nav.get(f"{{{NS['epub']}}}type", ""):
            return _parse_nav_ol(nav)

    # Fallback: first nav element
    nav = root.find(f".//{{{NS['xhtml']}}}nav")
    if nav is not None:
        return _parse_nav_ol(nav)
    return []


def _parse_nav_ol(nav_el: ET.Element) -> list[dict]:
    """Parse an ordered list inside a nav element."""
    ol = nav_el.find(f"{{{NS['xhtml']}}}ol")
    if ol is None:
        return []

    def parse_items(ol_el):
        entries = []
        for li in ol_el.findall(f"{{{NS['xhtml']}}}li"):
            a = li.find(f"{{{NS['xhtml']}}}a")
            if a is not None:
                label = "".join(a.itertext()).strip()
                entry = {"label": label, "src": a.get("href", "")}
            else:
                span = li.find(f"{{{NS['xhtml']}}}span")
                label = "".join(span.itertext()).strip() if span is not None else ""
                entry = {"label": label}

            child_ol = li.find(f"{{{NS['xhtml']}}}ol")
            if child_ol is not None:
                children = parse_items(child_ol)
                if children:
                    entry["children"] = children
            entries.append(entry)
        return entries

    return parse_items(ol)


def extract_epub_info(epub_path: str) -> dict:
    """Extract all structured information from an EPUB file."""
    result = {"file": epub_path}

    with zipfile.ZipFile(epub_path, "r") as zf:
        # Basic file listing
        result["files"] = sorted(zf.namelist())
        result["file_count"] = len(result["files"])
        result["total_size"] = sum(info.file_size for info in zf.infolist())

        # Find and parse OPF
        opf_path = find_opf_path(zf)
        result["opf_path"] = opf_path
        opf_dir = str(PurePosixPath(opf_path).parent)

        opf_xml = zf.read(opf_path)
        opf_root = ET.fromstring(opf_xml)

        # EPUB version
        result["version"] = opf_root.get("version", "unknown")

        # Metadata
        result["metadata"] = parse_metadata(opf_root)

        # Manifest
        manifest = parse_manifest(opf_root)
        result["manifest"] = manifest

        # Spine
        result["spine"] = parse_spine(opf_root)

        # Table of contents
        # Try EPUB 3 nav document first
        nav_item = next(
            (m for m in manifest if "nav" in m.get("properties", "")),
            None,
        )
        if nav_item:
            nav_path = str(PurePosixPath(opf_dir) / nav_item["href"]) if opf_dir != "." else nav_item["href"]
            result["toc"] = parse_toc_nav(zf, nav_path)
            result["toc_type"] = "epub3-nav"
        else:
            # Fall back to NCX
            ncx_item = next(
                (m for m in manifest if m.get("media-type") == "application/x-dtbncx+xml"),
                None,
            )
            if ncx_item:
                ncx_path = str(PurePosixPath(opf_dir) / ncx_item["href"]) if opf_dir != "." else ncx_item["href"]
                result["toc"] = parse_toc_ncx(zf, ncx_path)
                result["toc_type"] = "ncx"
            else:
                result["toc"] = []
                result["toc_type"] = "none"

    return result


def format_text(info: dict) -> str:
    """Format extracted info as human-readable text."""
    lines = []
    lines.append(f"EPUB: {info['file']}")
    lines.append(f"Version: {info['version']}")
    lines.append(f"Files: {info['file_count']} ({info['total_size']:,} bytes uncompressed)")
    lines.append(f"OPF: {info['opf_path']}")
    lines.append("")

    # Metadata
    meta = info.get("metadata", {})
    if meta:
        lines.append("── Metadata ──")
        for key, val in meta.items():
            if key in ("@context", "@type", "epubMeta"):
                continue
            display_key = key
            if display_key.startswith("dc:"):
                display_key = display_key[3:]
            elif display_key.startswith("dcterms:"):
                display_key = display_key[8:]
            if isinstance(val, list):
                for v in val:
                    lines.append(f"  {display_key}: {v}")
            else:
                lines.append(f"  {display_key}: {val}")
        lines.append("")

    # TOC
    toc = info.get("toc", [])
    if toc:
        lines.append(f"── Table of Contents ({info.get('toc_type', 'unknown')}) ──")

        def fmt_toc(entries, indent=1):
            for entry in entries:
                prefix = "  " * indent
                lines.append(f"{prefix}- {entry['label']}")
                if "children" in entry:
                    fmt_toc(entry["children"], indent + 1)

        fmt_toc(toc)
        lines.append("")

    # Spine
    spine = info.get("spine", [])
    if spine:
        lines.append(f"── Spine ({len(spine)} items) ──")
        manifest_map = {m["id"]: m for m in info.get("manifest", [])}
        for i, item in enumerate(spine, 1):
            idref = item["idref"]
            m = manifest_map.get(idref, {})
            href = m.get("href", "?")
            lines.append(f"  {i:3d}. {idref} → {href}")
        lines.append("")

    # Manifest summary by media type
    manifest = info.get("manifest", [])
    if manifest:
        type_counts = {}
        for m in manifest:
            mt = m.get("media-type", "unknown")
            type_counts[mt] = type_counts.get(mt, 0) + 1
        lines.append(f"── Manifest ({len(manifest)} items) ──")
        for mt, count in sorted(type_counts.items()):
            lines.append(f"  {mt}: {count}")
        lines.append("")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: epub_metadata.py <file.epub> [--json]", file=sys.stderr)
        sys.exit(1)

    epub_path = sys.argv[1]
    use_json = "--json" in sys.argv

    try:
        info = extract_epub_info(epub_path)
    except (zipfile.BadZipFile, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if use_json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
    else:
        print(format_text(info))


if __name__ == "__main__":
    main()
