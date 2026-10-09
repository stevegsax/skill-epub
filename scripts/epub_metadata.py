#!/usr/bin/env python3
"""Extract metadata, TOC, spine, manifest, and file locations from an EPUB.

Usage:
    uv run --no-project epub_metadata.py <file.epub>              # readable text
    uv run --no-project epub_metadata.py <file.epub> --json       # full JSON
    uv run --no-project epub_metadata.py <file.epub> --summary    # short JSON
    uv run --no-project epub_metadata.py <file.epub> --path opf   # one archive path

The JSON "metadata" object is JSON-LD built from an RDF graph (rdflib),
using the Dublin Core vocabularies plus the EPUB package vocabularies. It is
the input format of epub_update.py --metadata-file, so a file produced here
can be edited and applied back.

rdflib is declared below (PEP 723); `uv run` provisions it automatically.
"""

# /// script
# requires-python = ">=3.10"
# dependencies = ["rdflib>=7"]
# ///

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

from epub_common import (
    NS,
    XML_NS,
    EpubError,
    find_opf_path,
    opf_dir,
    resolve_href,
    split_properties,
)
from rdflib import RDF, BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DC, DCTERMS

# Vocabulary of unprefixed <meta property> values (EPUB 3.3 package
# metadata vocabulary). EPUBCheck uses this same URI.
META_VOCAB = "http://idpf.org/epub/vocab/package/meta/#"

# EPUB 3.3 reserved prefixes, usable without declaration in the package
# document. URIs match the EPUB 3.3 specification and EPUBCheck.
EPUB3_RESERVED_PREFIXES = {
    "a11y": "http://www.idpf.org/epub/vocab/package/a11y/#",
    "dcterms": "http://purl.org/dc/terms/",
    "marc": "http://id.loc.gov/vocabulary/",
    "media": "http://www.idpf.org/epub/vocab/overlays/#",
    "onix": "http://www.editeur.org/ONIX/book/codelists/current.html#",
    "rendition": "http://www.idpf.org/vocab/rendition/#",
    "schema": "http://schema.org/",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

# Attributes that EPUB 2 put directly on Dublin Core elements. They are
# expressed in JSON-LD under the "opf" prefix so they survive a round-trip.
OPF_ATTR_NS = "http://www.idpf.org/2007/opf#"
OPF2_ATTRIBUTES = ("role", "file-as", "scheme", "event")

DC_FIELDS = [
    "title",
    "creator",
    "subject",
    "description",
    "publisher",
    "contributor",
    "date",
    "type",
    "format",
    "identifier",
    "source",
    "language",
    "relation",
    "coverage",
    "rights",
]


def _parse_opf_prefixes(opf_root: ET.Element) -> dict[str, str]:
    """Prefix map: reserved prefixes overlaid by the package prefix attribute."""
    prefixes = dict(EPUB3_RESERVED_PREFIXES)
    for match in re.finditer(r"(\S+):\s+(\S+)", opf_root.get("prefix", "")):
        prefixes[match.group(1)] = match.group(2)
    return prefixes


def _resolve_property(prop_value: str, prefixes: dict[str, str]) -> URIRef:
    """Expand an OPF property string to a URI (unprefixed → meta vocabulary)."""
    if ":" in prop_value:
        prefix, local = prop_value.split(":", 1)
        if prefix in prefixes:
            return URIRef(prefixes[prefix] + local)
    return URIRef(META_VOCAB + prop_value)


def _literal(el: ET.Element) -> Literal:
    text = (el.text or "").strip()
    lang = el.get(f"{{{XML_NS}}}lang")
    return Literal(text, lang=lang) if lang else Literal(text)


def parse_metadata(opf_root: ET.Element) -> tuple[dict, list[dict]]:
    """Build the JSON-LD metadata object and the list of legacy meta elements.

    Dublin Core elements become dc:* predicates on the publication node.
    An element that something refines (<meta refines="#id">) becomes a
    blank node carrying rdf:value plus one predicate per refinement; a
    refinement with a scheme attribute is itself a blank node with
    rdf:value and opf:scheme. EPUB 2 attributes (opf:role, opf:file-as,
    opf:scheme, opf:event) become opf:* predicates. Non-refining
    <meta property> elements become predicates on the publication node.
    EPUB 2 <meta name content> elements are returned separately, since
    they are not RDF.
    """
    metadata_el = opf_root.find("opf:metadata", NS)
    if metadata_el is None:
        return {}, []

    prefixes = _parse_opf_prefixes(opf_root)
    OPF = Namespace(OPF_ATTR_NS)
    g = Graph()

    # Index every element with an id, and every refinement by target id.
    by_id: dict[str, ET.Element] = {}
    refinements: dict[str, list[ET.Element]] = {}
    legacy: list[dict] = []
    for el in list(metadata_el):
        if not isinstance(el.tag, str):
            continue
        el_id = el.get("id")
        if el_id:
            by_id[el_id] = el
        if el.tag == f"{{{NS['opf']}}}meta":
            refines = el.get("refines", "")
            if refines.startswith("#"):
                refinements.setdefault(refines[1:], []).append(el)
            elif el.get("name") is not None and el.get("property") is None:
                legacy.append({"name": el.get("name"), "content": el.get("content", "")})

    def node_for(el: ET.Element):
        """RDF object for an element: a Literal, or a BNode when refined."""
        el_id = el.get("id")
        refs = refinements.get(el_id, []) if el_id else []
        scheme = el.get("scheme")
        opf2 = [
            (a, el.get(f"{{{NS['opf']}}}{a}"))
            for a in OPF2_ATTRIBUTES
            if el.get(f"{{{NS['opf']}}}{a}") is not None
        ]
        if not refs and not scheme and not opf2:
            return _literal(el)
        node = BNode()
        g.add((node, RDF.value, _literal(el)))
        if scheme:
            g.add((node, OPF.scheme, Literal(scheme)))
        for attr, val in opf2:
            g.add((node, OPF[attr], Literal(val)))
        for ref in refs:
            prop = ref.get("property")
            if not prop or not (ref.text or "").strip():
                continue
            g.add((node, _resolve_property(prop, prefixes), node_for(ref)))
        return node

    # The publication node is the dc:identifier named by unique-identifier.
    uid = opf_root.get("unique-identifier")
    id_el = by_id.get(uid) if uid else None
    if id_el is None:
        id_el = metadata_el.find("dc:identifier", NS)
    id_text = (id_el.text or "").strip() if id_el is not None else ""
    pub = URIRef(id_text) if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", id_text) else BNode()
    g.add((pub, RDF.type, DCTERMS.BibliographicResource))

    for field in DC_FIELDS:
        for el in metadata_el.findall(f"dc:{field}", NS):
            if not (el.text or "").strip():
                continue
            g.add((pub, DC[field], node_for(el)))

    for meta in metadata_el.findall("opf:meta", NS):
        if meta.get("refines") or meta.get("property") is None:
            continue
        if not (meta.text or "").strip():
            continue
        g.add((pub, _resolve_property(meta.get("property"), prefixes), node_for(meta)))

    context = {
        "dc": str(DC),
        "dcterms": str(DCTERMS),
        "rdf": str(RDF),
        "epub": META_VOCAB,
        "opf": OPF_ATTR_NS,
    }
    for prefix, uri in prefixes.items():
        context.setdefault(prefix, uri)

    jsonld = json.loads(g.serialize(format="json-ld", context=context))
    result = _inline_bnodes(jsonld)
    _order_by_document(result, metadata_el)
    return result, legacy


def _inline_bnodes(jsonld: dict | list) -> dict:
    """Embed blank nodes from @graph where they are referenced."""
    if isinstance(jsonld, list):
        jsonld = {"@graph": jsonld, "@context": {}}
    if "@graph" not in jsonld:
        jsonld.pop("@id", None) if str(jsonld.get("@id", "")).startswith("_:") else None
        return jsonld
    graph = jsonld["@graph"]
    context = jsonld.get("@context", {})
    bnodes = {n["@id"]: n for n in graph if str(n.get("@id", "")).startswith("_:")}

    def embed(obj):
        if isinstance(obj, dict):
            nid = obj.get("@id", "")
            if nid in bnodes and len(obj) == 1:
                return {k: embed(v) for k, v in bnodes[nid].items() if k != "@id"}
            return {k: embed(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [embed(i) for i in obj]
        return obj

    root = next((n for n in graph if not str(n.get("@id", "")).startswith("_:")), None)
    if root is None:
        # Publication node is itself a blank node (non-URI identifier).
        referenced = {
            o["@id"]
            for n in graph
            for v in n.values()
            if isinstance(v, list)
            for o in v
            if isinstance(o, dict) and o.get("@id", "") in bnodes
        }
        referenced |= {
            v["@id"]
            for n in graph
            for v in n.values()
            if isinstance(v, dict) and v.get("@id", "") in bnodes
        }
        root = next(n for n in graph if n["@id"] not in referenced)
    result = embed(root)
    if str(result.get("@id", "")).startswith("_:"):
        result.pop("@id")
    result["@context"] = context
    return result


def _plain_text(value) -> str:
    if isinstance(value, dict):
        return _plain_text(value.get("rdf:value", value.get("@value", "")))
    return str(value)


def _order_by_document(result: dict, metadata_el: ET.Element) -> None:
    """Restore document order for multi-valued keys (RDF graphs are unordered)."""
    order: dict[str, list[str]] = {}
    for el in metadata_el:
        if not isinstance(el.tag, str):
            continue
        text = (el.text or "").strip()
        if el.tag.startswith(f"{{{NS['dc']}}}"):
            key = "dc:" + el.tag.split("}", 1)[1]
        elif el.get("property") and not el.get("refines"):
            key = el.get("property")
        else:
            continue
        order.setdefault(key, []).append(text)
    for key, values in result.items():
        if not isinstance(values, list):
            continue
        texts = order.get(key)
        if not texts:
            continue
        rank = {t: i for i, t in reversed(list(enumerate(texts)))}
        values.sort(key=lambda v: rank.get(_plain_text(v), len(texts)))


def parse_manifest(opf_root: ET.Element) -> list[dict]:
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
        props = split_properties(item.get("properties"))
        if props:
            entry["properties"] = props
        items.append(entry)
    return items


def parse_spine(opf_root: ET.Element) -> list[dict]:
    spine_el = opf_root.find("opf:spine", NS)
    if spine_el is None:
        return []
    items = []
    for itemref in spine_el.findall("opf:itemref", NS):
        entry = {"idref": itemref.get("idref", "")}
        if itemref.get("linear"):
            entry["linear"] = itemref.get("linear")
        props = split_properties(itemref.get("properties"))
        if props:
            entry["properties"] = props
        items.append(entry)
    return items


def parse_toc_ncx(zf: zipfile.ZipFile, ncx_path: str) -> list[dict]:
    root = ET.fromstring(zf.read(ncx_path))
    nav_map = root.find("ncx:navMap", NS)
    if nav_map is None:
        return []

    def walk(parent):
        entries = []
        for np in parent.findall("ncx:navPoint", NS):
            label_el = np.find("ncx:navLabel/ncx:text", NS)
            content_el = np.find("ncx:content", NS)
            entry = {
                "label": (label_el.text or "").strip() if label_el is not None else "",
                "src": content_el.get("src", "") if content_el is not None else "",
            }
            children = walk(np)
            if children:
                entry["children"] = children
            entries.append(entry)
        return entries

    return walk(nav_map)


def parse_toc_nav(zf: zipfile.ZipFile, nav_path: str) -> list[dict]:
    root = ET.fromstring(zf.read(nav_path))
    xh = f"{{{NS['xhtml']}}}"
    toc = None
    for nav in root.iter(f"{xh}nav"):
        if "toc" in split_properties(nav.get(f"{{{NS['epub']}}}type")):
            toc = nav
            break
    if toc is None:
        toc = root.find(f".//{xh}nav")
    if toc is None:
        return []

    def walk(ol):
        entries = []
        for li in ol.findall(f"{xh}li"):
            a = li.find(f"{xh}a")
            if a is not None:
                entry = {"label": "".join(a.itertext()).strip(), "src": a.get("href", "")}
            else:
                span = li.find(f"{xh}span")
                entry = {"label": "".join(span.itertext()).strip() if span is not None else ""}
            child = li.find(f"{xh}ol")
            if child is not None:
                children = walk(child)
                if children:
                    entry["children"] = children
            entries.append(entry)
        return entries

    ol = toc.find(f"{xh}ol")
    return walk(ol) if ol is not None else []


def extract_epub_info(epub_path: str) -> dict:
    """Extract all structured information from an EPUB file."""
    result: dict = {"file": epub_path}
    with zipfile.ZipFile(epub_path, "r") as zf:
        names = zf.namelist()
        result["files"] = sorted(names)
        result["file_count"] = len(names)
        result["total_size"] = sum(i.file_size for i in zf.infolist())

        opf_path = find_opf_path(zf)
        base = opf_dir(opf_path)
        try:
            opf_root = ET.fromstring(zf.read(opf_path))
        except ET.ParseError as e:
            raise EpubError(f"{opf_path} is not well-formed XML: {e}") from None

        result["version"] = opf_root.get("version", "unknown")
        result["metadata"], result["legacy_meta"] = parse_metadata(opf_root)
        manifest = parse_manifest(opf_root)
        result["manifest"] = manifest
        spine = parse_spine(opf_root)
        result["spine"] = spine

        by_id = {m["id"]: m for m in manifest}
        paths: dict = {"opf": opf_path, "opf_dir": base}

        nav_item = next((m for m in manifest if "nav" in m.get("properties", [])), None)
        ncx_item = next(
            (m for m in manifest if m.get("media-type") == "application/x-dtbncx+xml"), None
        )
        paths["nav"] = resolve_href(base, nav_item["href"]) if nav_item else None
        paths["ncx"] = resolve_href(base, ncx_item["href"]) if ncx_item else None

        cover_item = next((m for m in manifest if "cover-image" in m.get("properties", [])), None)
        if cover_item is None:
            cover_id = next(
                (lm["content"] for lm in result["legacy_meta"] if lm["name"] == "cover"), None
            )
            cover_item = by_id.get(cover_id) if cover_id else None
        paths["cover_image"] = resolve_href(base, cover_item["href"]) if cover_item else None
        paths["spine"] = [
            resolve_href(base, by_id[s["idref"]]["href"]) for s in spine if s["idref"] in by_id
        ]
        result["paths"] = paths

        missing = [p for p in [paths["nav"], paths["ncx"], *paths["spine"]] if p and p not in names]
        if missing:
            result["missing_files"] = missing

        result["toc"], result["toc_type"] = [], "none"
        if paths["nav"] and paths["nav"] in names:
            result["toc"], result["toc_type"] = parse_toc_nav(zf, paths["nav"]), "epub3-nav"
        elif paths["ncx"] and paths["ncx"] in names:
            result["toc"], result["toc_type"] = parse_toc_ncx(zf, paths["ncx"]), "ncx"
    return result


def summary(info: dict) -> dict:
    return {
        k: info[k]
        for k in (
            "file",
            "version",
            "metadata",
            "legacy_meta",
            "toc_type",
            "paths",
            "file_count",
            "total_size",
            "missing_files",
        )
        if k in info
    }


def format_text(info: dict) -> str:
    lines = [
        f"EPUB: {info['file']}",
        f"Version: {info['version']}",
        f"Files: {info['file_count']} ({info['total_size']:,} bytes uncompressed)",
        f"OPF: {info['paths']['opf']}",
    ]
    for key in ("nav", "ncx", "cover_image"):
        if info["paths"].get(key):
            lines.append(f"{key.replace('_', ' ').title()}: {info['paths'][key]}")
    if info.get("missing_files"):
        lines.append(f"Missing files: {', '.join(info['missing_files'])}")
    lines.append("")

    meta = info.get("metadata", {})
    if meta:
        lines.append("── Metadata ──")
        for key, val in meta.items():
            if key.startswith("@"):
                continue
            name = key.split(":", 1)[1] if ":" in key else key
            for v in val if isinstance(val, list) else [val]:
                text = _plain_text(v)
                extra = ""
                if isinstance(v, dict):
                    bits = [
                        f"{k.split(':', 1)[-1]}={_plain_text(x)}"
                        for k, x in v.items()
                        if k not in ("rdf:value", "@value", "@language")
                    ]
                    if bits:
                        extra = f"  ({', '.join(bits)})"
                lines.append(f"  {name}: {text}{extra}")
        lines.append("")
    if info.get("legacy_meta"):
        lines.append("── Legacy meta (EPUB 2 name/content) ──")
        for lm in info["legacy_meta"]:
            lines.append(f"  {lm['name']}: {lm['content']}")
        lines.append("")

    toc = info.get("toc", [])
    if toc:
        lines.append(f"── Table of Contents ({info.get('toc_type')}) ──")

        def fmt(entries, indent=1):
            for e in entries:
                lines.append(f"{'  ' * indent}- {e['label']}")
                if "children" in e:
                    fmt(e["children"], indent + 1)

        fmt(toc)
        lines.append("")

    spine = info.get("spine", [])
    if spine:
        lines.append(f"── Spine ({len(spine)} items) ──")
        by_id = {m["id"]: m for m in info.get("manifest", [])}
        for i, item in enumerate(spine, 1):
            lines.append(
                f"  {i:3d}. {item['idref']} → {by_id.get(item['idref'], {}).get('href', '?')}"
            )
        lines.append("")

    manifest = info.get("manifest", [])
    if manifest:
        counts: dict[str, int] = {}
        for m in manifest:
            counts[m.get("media-type", "unknown")] = (
                counts.get(m.get("media-type", "unknown"), 0) + 1
            )
        lines.append(f"── Manifest ({len(manifest)} items) ──")
        for mt, n in sorted(counts.items()):
            lines.append(f"  {mt}: {n}")
        lines.append("")
    return "\n".join(lines)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("epub", help="Path to the EPUB file")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--json",
        action="store_true",
        help="Full JSON output (metadata, manifest, spine, toc, files, paths)",
    )
    mode.add_argument(
        "--summary",
        action="store_true",
        help="Short JSON output: metadata, version, toc type, paths, file count",
    )
    mode.add_argument(
        "--path",
        choices=["opf", "nav", "ncx", "cover_image"],
        help="Print one archive path (for use with unzip -p); exit 2 if absent",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        info = extract_epub_info(args.epub)
    except FileNotFoundError:
        print(f"Error: file not found: {args.epub}", file=sys.stderr)
        return 1
    except (zipfile.BadZipFile, EpubError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.path:
        value = info["paths"].get(args.path)
        if not value:
            print(f"Error: this EPUB has no {args.path}", file=sys.stderr)
            return 2
        print(value)
    elif args.json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
    elif args.summary:
        print(json.dumps(summary(info), indent=2, ensure_ascii=False))
    else:
        print(format_text(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
