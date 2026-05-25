#!/usr/bin/env python3
"""Extract and display metadata, TOC, spine, and manifest from an EPUB file.

Usage:
    uv run epub_metadata.py <file.epub> [--json]

Outputs structured metadata to stdout. Use --json for machine-readable output.
rdflib (RDF graph construction + JSON-LD serialization) is declared inline via
PEP 723, so `uv run` provisions it automatically — no separate install needed.
"""

# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib"]
# ///

import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

from rdflib import Graph, URIRef, Literal, BNode, Namespace, RDF
from rdflib.namespace import DC, DCTERMS


# XML namespaces used in EPUB
NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
}

# EPUB 3 default vocabulary for unprefixed property values
EPUB_DEFAULT_VOCAB = "http://idpf.org/epub/vocab/package/#"

# EPUB 3 reserved prefixes (always available without declaration)
EPUB3_RESERVED_PREFIXES = {
    "dcterms": "http://purl.org/dc/terms/",
    "marc": "http://id.loc.gov/vocabulary/relators/",
    "media": "http://www.idpf.org/epub/vocab/overlays/#",
    "onix": "http://www.editeur.org/ONIX/book/codelists/current.html#",
    "rendition": "http://www.idpf.org/vocab/rendition/#",
    "schema": "https://schema.org/",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "a11y": "http://www.idpf.org/epub/vocab/package/a11y/#",
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


def _parse_opf_prefixes(opf_root: ET.Element) -> dict[str, str]:
    """Parse the <package prefix="..."> attribute into a prefix-to-URI dict.

    Includes EPUB 3 reserved prefixes as defaults, then overlays any
    prefixes declared in the OPF package element.
    """
    prefixes = dict(EPUB3_RESERVED_PREFIXES)
    prefix_attr = opf_root.get("prefix", "")
    if prefix_attr:
        for match in re.finditer(r"(\S+):\s+(\S+)", prefix_attr):
            prefixes[match.group(1)] = match.group(2)
    return prefixes


def _resolve_property(prop_value: str, prefixes: dict[str, str]) -> URIRef:
    """Resolve an OPF property string to a full URIRef.

    Prefixed values (e.g., "dcterms:modified") are expanded using the
    prefix map. Unprefixed values use the EPUB default vocabulary.
    """
    if ":" in prop_value:
        prefix, local = prop_value.split(":", 1)
        if prefix in prefixes:
            return URIRef(prefixes[prefix] + local)
    return URIRef(EPUB_DEFAULT_VOCAB + prop_value)


def _inline_bnodes(jsonld: dict) -> dict:
    """Inline blank-node entries from @graph into their parent references.

    rdflib serializes BNodes as separate @graph entries with references like
    {"@id": "_:N..."}.  This function embeds them at the point of reference
    and strips the internal BNode @id values.
    """
    if not isinstance(jsonld, dict) or "@graph" not in jsonld:
        return jsonld

    graph = jsonld["@graph"]
    context = jsonld.get("@context", {})

    # Index BNodes by @id
    bnodes: dict[str, dict] = {}
    for node in graph:
        nid = node.get("@id", "")
        if nid.startswith("_:"):
            bnodes[nid] = node

    def _embed(obj):
        """Recursively replace BNode references with their content."""
        if isinstance(obj, dict):
            nid = obj.get("@id", "")
            if nid.startswith("_:") and nid in bnodes and len(obj) == 1:
                # This is a bare reference — replace with the full BNode
                embedded = {k: _embed(v) for k, v in bnodes[nid].items()
                            if k != "@id"}
                return embedded
            return {k: _embed(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_embed(item) for item in obj]
        return obj

    # Find the publication node (non-BNode) and embed BNodes into it
    for node in graph:
        nid = node.get("@id", "")
        if not nid.startswith("_:"):
            result = _embed(node)
            result["@context"] = context
            return result

    # Fallback: return first node
    if graph:
        result = _embed(graph[0])
        result.pop("@id", None)
        result["@context"] = context
        return result

    return jsonld


def parse_metadata(opf_root: ET.Element) -> dict:
    """Extract metadata from the OPF package document as JSON-LD.

    Uses rdflib to build an RDF graph from OPF metadata, then serializes
    to JSON-LD. DC elements with <meta refines="#id"> become structured
    nodes (BNodes with rdf:value + refinement predicates). Non-refining
    <meta property="..."> elements become direct predicates on the
    publication node.
    """
    metadata_el = opf_root.find("opf:metadata", NS)
    if metadata_el is None:
        return {}

    prefixes = _parse_opf_prefixes(opf_root)

    EPUB_NS = Namespace(EPUB_DEFAULT_VOCAB)
    SCHEMA = Namespace("https://schema.org/")

    g = Graph()
    g.bind("dc", DC)
    g.bind("dcterms", DCTERMS)
    g.bind("epub", EPUB_NS)
    g.bind("schema", SCHEMA)

    # Build refinements index: {element_id: [(property_uri, value), ...]}
    refinements: dict[str, list[tuple[URIRef, str]]] = {}
    for meta in metadata_el.findall("opf:meta", NS):
        refines = meta.get("refines", "")
        if refines and refines.startswith("#"):
            el_id = refines[1:]
            prop = meta.get("property", "")
            if prop and meta.text and meta.text.strip():
                prop_uri = _resolve_property(prop, prefixes)
                refinements.setdefault(el_id, []).append(
                    (prop_uri, meta.text.strip())
                )

    # Determine publication node from dc:identifier
    id_el = metadata_el.find("dc:identifier", NS)
    if id_el is not None and id_el.text and ":" in id_el.text.strip():
        pub = URIRef(id_el.text.strip())
    else:
        pub = BNode()

    g.add((pub, RDF.type, DCTERMS.BibliographicResource))

    # Process DC elements
    dc_fields = [
        "title", "creator", "subject", "description", "publisher",
        "contributor", "date", "type", "format", "identifier",
        "source", "language", "relation", "coverage", "rights",
    ]
    for field in dc_fields:
        for el in metadata_el.findall(f"dc:{field}", NS):
            if not el.text or not el.text.strip():
                continue
            text = el.text.strip()
            el_id = el.get("id")
            dc_pred = DC[field]

            if el_id and el_id in refinements:
                node = BNode()
                g.add((pub, dc_pred, node))
                g.add((node, RDF.value, Literal(text)))
                for prop_uri, val in refinements[el_id]:
                    g.add((node, prop_uri, Literal(val)))
            else:
                g.add((pub, dc_pred, Literal(text)))

    # Non-refining <meta property="..."> elements become direct predicates
    for meta in metadata_el.findall("opf:meta", NS):
        if meta.get("refines"):
            continue
        prop = meta.get("property", "")
        if not prop or not meta.text or not meta.text.strip():
            continue
        prop_uri = _resolve_property(prop, prefixes)
        g.add((pub, prop_uri, Literal(meta.text.strip())))

    # Serialize to JSON-LD
    context = {
        "dc": str(DC),
        "dcterms": str(DCTERMS),
        "rdf": str(RDF),
        "epub": EPUB_DEFAULT_VOCAB,
        "schema": "https://schema.org/",
    }

    jsonld_str = g.serialize(format="json-ld", context=context)
    result = _inline_bnodes(json.loads(jsonld_str))
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
            if key.startswith("@"):
                continue
            display_key = key.split(":", 1)[1] if ":" in key else key
            vals = val if isinstance(val, list) else [val]
            for v in vals:
                text = v.get("rdf:value", str(v)) if isinstance(v, dict) else str(v)
                lines.append(f"  {display_key}: {text}")
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
