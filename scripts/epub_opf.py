"""lxml-based editing of EPUB package documents (OPF) and navigation files.

Shared by epub_update.py and epub_upgrade.py. lxml is used, rather than the
standard library's ElementTree, because it keeps the namespace prefixes and
comments of the source document when it serializes. ElementTree rewrites
prefixes, and it drops the opf: prefix from attributes such as opf:role,
which makes an EPUB 2 package invalid.

Every function here edits the parsed tree in place and returns a list of
human-readable change descriptions, so the calling script can report what
it did.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from urllib.parse import quote, urlsplit

from epub_common import (
    DC_NS,
    EPUB_OPS_NS,
    NCX_NS,
    OPF_NS,
    XHTML_NS,
    XML_NS,
    EpubError,
    is_iso_date,
    is_modified_timestamp,
    relative_href,
    resolve_href,
    split_properties,
)
from lxml import etree

META_VOCAB = "http://idpf.org/epub/vocab/package/meta/#"
OPF_ATTR_NS = "http://www.idpf.org/2007/opf#"

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

# JSON-LD context prefixes that epub_metadata.py emits but that are not
# OPF prefixes. "epub" is the default (unprefixed) meta vocabulary.
JSONLD_ONLY_PREFIXES = {
    "epub": META_VOCAB,
    "opf": OPF_ATTR_NS,
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": DC_NS,
}

# dcterms properties whose values are dates. They are validated as ISO 8601.
DCTERMS_DATE_PROPERTIES = {
    "dcterms:modified",
    "dcterms:created",
    "dcterms:issued",
    "dcterms:date",
    "dcterms:available",
    "dcterms:dateAccepted",
    "dcterms:dateCopyrighted",
    "dcterms:dateSubmitted",
    "dcterms:valid",
}

OPF2_ATTRIBUTES = ("role", "file-as", "scheme", "event")

XML_PARSER = etree.XMLParser(
    remove_blank_text=False, resolve_entities=False, no_network=True, huge_tree=True
)


def qn(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


OPF_META = qn(OPF_NS, "meta")
OPF_ITEM = qn(OPF_NS, "item")
OPF_ITEMREF = qn(OPF_NS, "itemref")
OPF_REFERENCE = qn(OPF_NS, "reference")


# ---------------------------------------------------------------------------
# Loading and saving
# ---------------------------------------------------------------------------


def load_xml(data: bytes) -> etree._Element:
    try:
        return etree.fromstring(data, parser=XML_PARSER)
    except etree.XMLSyntaxError as e:
        raise EpubError(f"not well-formed XML: {e}") from None


def dump_xml(root: etree._Element) -> bytes:
    """Serialize a tree, keeping its DOCTYPE and declaring the XML header."""
    doctype = root.getroottree().docinfo.doctype or None
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", doctype=doctype)


def load_opf(data: bytes) -> etree._Element:
    root = load_xml(data)
    if root.tag != qn(OPF_NS, "package"):
        raise EpubError(f"package document root is {root.tag}, expected an OPF <package>")
    return root


def dump_opf(root: etree._Element) -> bytes:
    """Serialize the OPF, making sure dc: and opf: prefixes are declared.

    cleanup_namespaces moves the prefix declarations we need to the root
    and removes declarations that nothing uses any more (for example an
    xmlns:opf left behind after opf:role attributes were converted).
    """
    etree.cleanup_namespaces(root, top_nsmap={"dc": DC_NS, "opf": OPF_NS}, keep_ns_prefixes=["dc"])
    return dump_xml(root)


def opf_version(root: etree._Element) -> str:
    return root.get("version", "")


def is_epub3(root: etree._Element) -> bool:
    return opf_version(root).startswith("3")


def metadata_el(root: etree._Element) -> etree._Element:
    el = root.find(qn(OPF_NS, "metadata"))
    if el is None:
        raise EpubError("package document has no <metadata> element")
    return el


def manifest_el(root: etree._Element) -> etree._Element:
    el = root.find(qn(OPF_NS, "manifest"))
    if el is None:
        raise EpubError("package document has no <manifest> element")
    return el


def spine_el(root: etree._Element) -> etree._Element:
    el = root.find(qn(OPF_NS, "spine"))
    if el is None:
        raise EpubError("package document has no <spine> element")
    return el


def parse_prefixes(root: etree._Element) -> dict[str, str]:
    prefixes = dict(EPUB3_RESERVED_PREFIXES)
    for m in re.finditer(r"(\S+):\s+(\S+)", root.get("prefix", "")):
        prefixes[m.group(1)] = m.group(2)
    return prefixes


def all_ids(root: etree._Element) -> set[str]:
    return {el.get("id") for el in root.iter() if isinstance(el.tag, str) and el.get("id")}


def unique_id(root: etree._Element, base: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-") or "id"
    if not re.match(r"[A-Za-z_]", base):
        base = "id-" + base
    ids = all_ids(root)
    if base not in ids:
        return base
    n = 2
    while f"{base}-{n}" in ids:
        n += 1
    return f"{base}-{n}"


def _insert_after(ref: etree._Element, new: etree._Element) -> None:
    """Insert new after ref, copying ref's tail so indentation stays even."""
    new.tail = ref.tail
    ref.addnext(new)


def _append_child(parent: etree._Element, new: etree._Element) -> None:
    """Append new as the last child, keeping the parent's indentation style.

    The last child's tail is the whitespace before the parent's closing
    tag; the new element inherits it, and the old last child gets the
    separator the parent uses between children (its leading text).
    """
    children = list(parent)
    if children:
        last = children[-1]
        closing = last.tail
        separator = parent.text if (parent.text and not parent.text.strip()) else "\n"
        last.tail = separator
        last.addnext(new)
        new.tail = closing
    else:
        parent.append(new)


def _cluster_end(meta: etree._Element, el: etree._Element) -> etree._Element:
    """Last element, in document order, of el and everything that refines it."""
    last = el
    for ref in refinements_of(meta, el.get("id")):
        candidate = _cluster_end(meta, ref)
        if meta.index(candidate) > meta.index(last):
            last = candidate
    return last


# ---------------------------------------------------------------------------
# Metadata: Dublin Core elements, meta properties, refinements
# ---------------------------------------------------------------------------


def refinements_of(meta: etree._Element, el_id: str | None) -> list[etree._Element]:
    if not el_id:
        return []
    return [m for m in meta.findall(OPF_META) if m.get("refines") == f"#{el_id}"]


def remove_with_refinements(meta: etree._Element, el: etree._Element) -> None:
    for ref in refinements_of(meta, el.get("id")):
        remove_with_refinements(meta, ref)
    _remove_keep_tail(el)


def _remove_keep_tail(el: etree._Element) -> None:
    parent = el.getparent()
    prev = el.getprevious()
    if prev is not None:
        prev.tail = el.tail
    elif parent is not None:
        parent.text = el.tail
    parent.remove(el)


def property_string(key: str, prefixes: dict[str, str]) -> str:
    """Turn a JSON-LD key back into the string that goes in property="..."."""
    if key.startswith("epub:"):
        return key[len("epub:") :]
    if "://" in key or key.startswith("urn:"):
        for prefix, uri in prefixes.items():
            if key.startswith(uri):
                return f"{prefix}:{key[len(uri) :]}"
        if key.startswith(META_VOCAB):
            return key[len(META_VOCAB) :]
        raise EpubError(
            f"cannot express {key!r} as an OPF property: no declared prefix matches it. "
            "Add a prefix for it to the package prefix attribute first."
        )
    prefix = key.split(":", 1)[0] if ":" in key else None
    if prefix and prefix not in prefixes and prefix not in ("dc",):
        raise EpubError(
            f"property {key!r} uses the prefix {prefix!r}, which is neither reserved "
            "by EPUB 3 nor declared in the package prefix attribute."
        )
    return key


def _dc_local(key: str) -> str | None:
    if key.startswith("dc:"):
        return key[3:]
    if key.startswith(DC_NS):
        return key[len(DC_NS) :]
    return None


def node_text(value) -> str:
    if isinstance(value, dict):
        return node_text(value.get("rdf:value", value.get("@value", "")))
    if value is None:
        return ""
    return str(value)


def validate_date(key: str, value: str) -> None:
    if key == "dcterms:modified":
        if not is_modified_timestamp(value):
            raise EpubError(f"dcterms:modified must look like 2026-01-31T12:00:00Z, got {value!r}")
    elif key == "dc:date" or key in DCTERMS_DATE_PROPERTIES:
        if not is_iso_date(value):
            raise EpubError(
                f"{key} must be an ISO 8601 date (2026, 2026-01, 2026-01-31 or "
                f"2026-01-31T12:00:00Z), got {value!r}"
            )


def _apply_node(
    meta: etree._Element,
    el: etree._Element,
    value,
    key: str,
    root: etree._Element,
    prefixes: dict[str, str],
) -> None:
    """Set an element's text and attributes/refinements from a JSON-LD value.

    A plain string sets the text. A dict sets rdf:value or @value as text,
    @language as xml:lang, opf:* keys as EPUB 2 attributes (on an EPUB 2
    package) or their EPUB 3 refinement equivalents (on an EPUB 3 package),
    and every other key as a <meta refines> element.
    """
    text = node_text(value)
    validate_date(key, text)
    el.text = text

    # A plain string changes the text only: existing refinements and
    # attributes (a role, a file-as form) are kept, so that renaming an
    # author never silently drops their role. A dict describes the whole
    # node, so what the dict leaves out is removed.
    if not isinstance(value, dict):
        return
    for ref in refinements_of(meta, el.get("id")):
        remove_with_refinements(meta, ref)
    for attr in OPF2_ATTRIBUTES:
        el.attrib.pop(qn(OPF_NS, attr), None)
    el.attrib.pop(qn(XML_NS, "lang"), None)
    if el.tag == OPF_META:
        el.attrib.pop("scheme", None)
    if "@language" in value:
        el.set(qn(XML_NS, "lang"), str(value["@language"]))

    epub3 = is_epub3(root)
    opf2 = {k[4:]: node_text(v) for k, v in value.items() if k.startswith("opf:")}
    refinements = {
        k: v
        for k, v in value.items()
        if k not in ("rdf:value", "@value", "@language", "@id", "@type")
        and not k.startswith("opf:")
    }

    scheme = opf2.pop("scheme", None)
    if el.tag == OPF_META and scheme is not None:
        el.set("scheme", scheme)
        scheme = None

    if opf2 or scheme is not None:
        if epub3:
            converted = convert_opf2_values(opf2, scheme, el)
            refinements = {**converted, **refinements}
        else:
            for attr, val in opf2.items():
                el.set(qn(OPF_NS, attr), val)
            if scheme is not None:
                el.set(qn(OPF_NS, "scheme"), scheme)

    if refinements:
        if not epub3:
            raise EpubError(
                f"{key}: refinements ({', '.join(refinements)}) need <meta refines>, "
                "which EPUB 2 does not allow. Upgrade the file with epub_upgrade.py first."
            )
        if not el.get("id"):
            el.set("id", unique_id(root, key.replace(":", "-")))
        anchor = el
        for rkey, rval in refinements.items():
            for one in rval if isinstance(rval, list) else [rval]:
                m = etree.Element(OPF_META)
                m.set("refines", f"#{el.get('id')}")
                m.set("property", property_string(rkey, prefixes))
                _insert_after(anchor, m)
                anchor = m
                _apply_node(meta, m, one, rkey, root, prefixes)


def convert_opf2_values(opf2: dict[str, str], scheme: str | None, el: etree._Element) -> dict:
    """EPUB 2 attribute values → EPUB 3 refinement nodes.

    role → role (scheme marc:relators); file-as → file-as; scheme on an
    identifier → identifier-type; event other than publication → a
    dcterms date property on the publication is not expressible as a
    refinement, so it is reported by the caller instead.
    """
    out: dict = {}
    if "role" in opf2:
        out["epub:role"] = {"rdf:value": opf2["role"], "opf:scheme": "marc:relators"}
    if "file-as" in opf2:
        out["epub:file-as"] = opf2["file-as"]
    if scheme is not None:
        if el.tag != qn(DC_NS, "identifier"):
            raise EpubError(
                f"opf:scheme={scheme!r} on {el.tag.split('}', 1)[1]} has no EPUB 3 "
                "equivalent; remove the key or keep the file as EPUB 2."
            )
        out["epub:identifier-type"] = scheme
    if "event" in opf2 and opf2["event"] != "publication":
        # dc:date in EPUB 3 is the publication date only; other events are
        # expressed as dcterms properties by convert_opf2_attributes().
        raise EpubError(
            f"dc:date with opf:event={opf2['event']!r} cannot be set on an EPUB 3 "
            "package; use dcterms:created or dcterms:modified instead."
        )
    return out


def set_dc(root: etree._Element, local: str, values: list, prefixes: dict[str, str]) -> list[str]:
    """Set all dc:<local> elements to values, editing existing ones in place."""
    meta = metadata_el(root)
    tag = qn(DC_NS, local)
    existing = meta.findall(tag)

    # Pair each value with an existing element: first by equal text, so an
    # element keeps its id and refinements when the list around it changes
    # (and the unique-identifier element keeps its identifier), then by
    # position for whatever is left.
    assigned: dict[int, etree._Element] = {}
    free_values = list(range(len(values)))
    free_elements = []
    for el in existing:
        text = (el.text or "").strip()
        match = next((i for i in free_values if node_text(values[i]) == text), None)
        if match is None:
            free_elements.append(el)
        else:
            assigned[match] = el
            free_values.remove(match)
    for i, el in zip(free_values, free_elements, strict=False):
        assigned[i] = el
    surplus = free_elements[len(free_values) :]

    changes = []
    anchor = existing[-1] if existing else None
    for i, value in enumerate(values):
        el = assigned.get(i)
        if el is None:
            el = etree.Element(tag, nsmap={"dc": DC_NS})
            if anchor is not None:
                _insert_after(_cluster_end(meta, anchor), el)
            else:
                _append_child(meta, el)
            anchor = el
        _apply_node(meta, el, value, f"dc:{local}", root, prefixes)
        changes.append(f"Set dc:{local}: {node_text(value)}")
    for el in surplus:
        if local == "identifier" and root.get("unique-identifier") == el.get("id"):
            raise EpubError("refusing to remove the dc:identifier named by unique-identifier")
        changes.append(f"Removed dc:{local}: {(el.text or '').strip()}")
        remove_with_refinements(meta, el)
    return changes


def set_property(
    root: etree._Element, key: str, values: list, prefixes: dict[str, str]
) -> list[str]:
    """Set all non-refining <meta property=key> elements to values."""
    meta = metadata_el(root)
    if not is_epub3(root):
        raise EpubError(
            f"{key}: <meta property> is not valid in an EPUB 2 package. "
            "Upgrade the file with epub_upgrade.py first."
        )
    prop = property_string(key, prefixes)
    existing = [
        m for m in meta.findall(OPF_META) if m.get("property") == prop and not m.get("refines")
    ]
    changes = []
    anchor = existing[-1] if existing else None
    for i, value in enumerate(values):
        if i < len(existing):
            el = existing[i]
        else:
            el = etree.Element(OPF_META)
            el.set("property", prop)
            if anchor is not None:
                _insert_after(anchor, el)
            else:
                _append_child(meta, el)
            anchor = el
        _apply_node(meta, el, value, key, root, prefixes)
        changes.append(f"Set {prop}: {node_text(value)}")
    for el in existing[len(values) :]:
        changes.append(f"Removed {prop}: {(el.text or '').strip()}")
        remove_with_refinements(meta, el)
    return changes


def remove_key(root: etree._Element, key: str, prefixes: dict[str, str]) -> list[str]:
    local = _dc_local(key)
    if local:
        return set_dc(root, local, [], prefixes)
    meta = metadata_el(root)
    prop = property_string(key, prefixes)
    changes = []
    for el in [
        m for m in meta.findall(OPF_META) if m.get("property") == prop and not m.get("refines")
    ]:
        changes.append(f"Removed {prop}: {(el.text or '').strip()}")
        remove_with_refinements(meta, el)
    return changes


def apply_metadata(root: etree._Element, updates: dict) -> list[str]:
    """Apply a JSON-LD metadata object (or a dict of key → value(s)).

    Keys present in updates are set; keys absent are left untouched. An
    empty list removes every element for that key.
    """
    prefixes = parse_prefixes(root)
    changes: list[str] = []
    for key, value in updates.items():
        if key.startswith("@"):
            continue
        values = value if isinstance(value, list) else [value]
        local = _dc_local(key)
        if local:
            changes += set_dc(root, local, values, prefixes)
        else:
            changes += set_property(root, key, values, prefixes)
    return changes


def set_modified(root: etree._Element, value: str) -> list[str]:
    """Set dcterms:modified (EPUB 3 only; EPUB 2 has no such element)."""
    if not is_epub3(root):
        return []
    validate_date("dcterms:modified", value)
    return set_property(root, "dcterms:modified", [value], parse_prefixes(root))


def convert_opf2_attributes(root: etree._Element) -> list[str]:
    """Replace EPUB 2 opf:* attributes on DC elements with EPUB 3 equivalents.

    Used when upgrading a package to EPUB 3. Each converted attribute is
    described in the returned list; nothing is silently dropped.
    """
    meta = metadata_el(root)
    prefixes = parse_prefixes(root)
    changes = []
    extra_dates: list[tuple[str, str]] = []
    dates_seen = 0
    for el in list(meta):
        if not isinstance(el.tag, str) or not el.tag.startswith(f"{{{DC_NS}}}"):
            continue
        local = el.tag.split("}", 1)[1]
        attrs = {
            a: el.get(qn(OPF_NS, a)) for a in OPF2_ATTRIBUTES if el.get(qn(OPF_NS, a)) is not None
        }
        if local == "date":
            dates_seen += 1
            event = attrs.pop("event", None)
            if event is None and dates_seen == 1:
                pass
            elif event == "publication" and dates_seen == 1:
                changes.append('dc:date: dropped opf:event="publication" (implied in EPUB 3)')
            else:
                # EPUB 3 allows a single dc:date, the publication date.
                # Other dated events move to dcterms properties.
                prop = {"creation": "dcterms:created", "publication": "dcterms:issued"}.get(
                    event or ""
                )
                text = (el.text or "").strip()
                if event == "modification":
                    changes.append(
                        f'dc:date opf:event="modification" ({text}) dropped: '
                        "dcterms:modified is set to the upgrade time instead"
                    )
                elif prop and is_iso_date(text):
                    extra_dates.append((prop, text))
                    changes.append(f"dc:date opf:event={event!r} moved to {prop}")
                else:
                    changes.append(
                        f"dc:date opf:event={event!r} value {text!r} dropped: EPUB 3 "
                        "allows one dc:date and this value has no dcterms equivalent"
                    )
                remove_with_refinements(meta, el)
                continue
            for a in list(el.attrib):
                if a == qn(OPF_NS, "event"):
                    del el.attrib[a]
            continue
        if not attrs:
            continue
        node: dict = {"rdf:value": (el.text or "").strip()}
        if el.get(qn(XML_NS, "lang")):
            node["@language"] = el.get(qn(XML_NS, "lang"))
        for a, v in attrs.items():
            if a == "scheme" and local != "identifier":
                changes.append(f"dc:{local}: dropped opf:scheme={v!r} (EPUB 3 has no equivalent)")
                continue
            node[f"opf:{a}"] = v
        _apply_node(meta, el, node, f"dc:{local}", root, prefixes)
        changes.append(
            f"dc:{local}: converted {', '.join('opf:' + a for a in attrs)} to EPUB 3 refinements"
        )
    for prop, text in extra_dates:
        set_property(root, prop, [text], prefixes)
    return changes


def drop_empty_dc(root: etree._Element) -> list[str]:
    meta = metadata_el(root)
    changes = []
    for el in list(meta):
        if (
            isinstance(el.tag, str)
            and el.tag.startswith(f"{{{DC_NS}}}")
            and not (el.text or "").strip()
        ):
            changes.append(f"Removed empty {el.tag.split('}', 1)[1]} element")
            remove_with_refinements(meta, el)
    return changes


# ---------------------------------------------------------------------------
# Manifest, spine, guide
# ---------------------------------------------------------------------------


def item_path(root: etree._Element, base: str, item: etree._Element) -> str:
    return resolve_href(base, item.get("href", ""))


def find_item_by_path(root: etree._Element, base: str, path: str) -> etree._Element | None:
    for item in manifest_el(root).findall(OPF_ITEM):
        if item_path(root, base, item) == path:
            return item
    return None


def find_nav_item(root: etree._Element) -> etree._Element | None:
    for item in manifest_el(root).findall(OPF_ITEM):
        if "nav" in split_properties(item.get("properties")):
            return item
    return None


def find_ncx_item(root: etree._Element) -> etree._Element | None:
    for item in manifest_el(root).findall(OPF_ITEM):
        if item.get("media-type") == "application/x-dtbncx+xml":
            return item
    return None


def find_cover_item(root: etree._Element) -> etree._Element | None:
    manifest = manifest_el(root)
    for item in manifest.findall(OPF_ITEM):
        if "cover-image" in split_properties(item.get("properties")):
            return item
    for m in metadata_el(root).findall(OPF_META):
        if m.get("name") == "cover" and m.get("content"):
            for item in manifest.findall(OPF_ITEM):
                if item.get("id") == m.get("content"):
                    return item
    return None


def add_manifest_item(
    root: etree._Element,
    base: str,
    path: str,
    media_type: str,
    item_id: str | None = None,
    properties: str | None = None,
) -> etree._Element:
    manifest = manifest_el(root)
    stem = posixpath.splitext(posixpath.basename(path))[0]
    item = etree.Element(OPF_ITEM)
    item.set("id", unique_id(root, item_id or stem))
    item.set("href", quote(relative_href(base, path)))
    item.set("media-type", media_type)
    if properties:
        item.set("properties", properties)
    _append_child(manifest, item)
    return item


def add_spine_item(root: etree._Element, item_id: str) -> None:
    spine = spine_el(root)
    ref = etree.Element(OPF_ITEMREF)
    ref.set("idref", item_id)
    _append_child(spine, ref)


def remove_manifest_item(root: etree._Element, item: etree._Element) -> list[str]:
    """Remove an item and every package reference to its id."""
    changes = [f"Removed manifest item {item.get('id')} ({item.get('href')})"]
    item_id = item.get("id")
    spine = root.find(qn(OPF_NS, "spine"))
    if spine is not None:
        for ref in spine.findall(OPF_ITEMREF):
            if ref.get("idref") == item_id:
                _remove_keep_tail(ref)
                changes.append(f"Removed spine itemref {item_id}")
        if spine.get("toc") == item_id:
            del spine.attrib["toc"]
            changes.append("Removed spine toc attribute")
    for m in metadata_el(root).findall(OPF_META):
        if m.get("name") == "cover" and m.get("content") == item_id:
            _remove_keep_tail(m)
            changes.append('Removed <meta name="cover">')
    guide = root.find(qn(OPF_NS, "guide"))
    if guide is not None:
        href = item.get("href", "").split("#")[0]
        for ref in guide.findall(OPF_REFERENCE):
            if ref.get("href", "").split("#")[0] == href:
                _remove_keep_tail(ref)
                changes.append(f"Removed guide reference {ref.get('type')}")
    _remove_keep_tail(item)
    return changes


def set_cover_marker(root: etree._Element, item: etree._Element) -> list[str]:
    """Mark item as the cover image in the way the package version expects."""
    changes = []
    if is_epub3(root):
        props = split_properties(item.get("properties"))
        if "cover-image" not in props:
            item.set("properties", " ".join(props + ["cover-image"]))
            changes.append(f'Added properties="cover-image" to {item.get("id")}')
    meta = metadata_el(root)
    legacy = [m for m in meta.findall(OPF_META) if m.get("name") == "cover"]
    if legacy:
        if legacy[0].get("content") != item.get("id"):
            legacy[0].set("content", item.get("id"))
            changes.append('Updated <meta name="cover">')
    elif not is_epub3(root):
        m = etree.Element(OPF_META)
        m.set("name", "cover")
        m.set("content", item.get("id"))
        _append_child(meta, m)
        changes.append('Added <meta name="cover">')
    return changes


# ---------------------------------------------------------------------------
# References inside content documents
# ---------------------------------------------------------------------------

REF_ATTR_RE = re.compile(
    r"""(?P<attr>\b(?:src|href|xlink:href|poster|data)\s*=\s*)(?P<q>["'])(?P<val>[^"']*)(?P=q)"""
)
CSS_URL_RE = re.compile(r"""url\(\s*(?P<q>["']?)(?P<val>[^)"']+)(?P=q)\s*\)""")

TEXT_MEDIA_TYPES = (
    "application/xhtml+xml",
    "text/css",
    "image/svg+xml",
    "application/smil+xml",
    "application/x-dtbncx+xml",
)


def _text_items(root: etree._Element, base: str, zf: zipfile.ZipFile):
    for item in manifest_el(root).findall(OPF_ITEM):
        if item.get("media-type") in TEXT_MEDIA_TYPES:
            path = item_path(root, base, item)
            if path in zf.namelist():
                yield item, path


def find_references(
    root: etree._Element,
    base: str,
    zf: zipfile.ZipFile,
    target: str,
    exclude: set[str] = frozenset(),
    overlay: dict[str, bytes] | None = None,
) -> list[tuple[str, str]]:
    """Return (document path, href as written) for each reference to target.

    overlay holds files already rewritten in this run; their new contents
    are scanned instead of the archive's.
    """
    found = []
    for item, path in _text_items(root, base, zf):
        if path in exclude or path == target:
            continue
        data = overlay.get(path) if overlay else None
        text = (data if data is not None else zf.read(path)).decode("utf-8", errors="replace")
        doc_dir = posixpath.dirname(path)
        pattern = CSS_URL_RE if item.get("media-type") == "text/css" else REF_ATTR_RE
        for m in pattern.finditer(text):
            val = m.group("val")
            if urlsplit(val).scheme or val.startswith("#"):
                continue
            if resolve_href(doc_dir, val) == target:
                found.append((path, val))
    return found


def rewrite_references(
    root: etree._Element, base: str, zf: zipfile.ZipFile, old: str, new: str
) -> dict[str, bytes]:
    """Rewrite every reference to old so it points at new. Returns new file bytes."""
    out: dict[str, bytes] = {}
    for item, path in _text_items(root, base, zf):
        if path == old:
            continue
        text = zf.read(path).decode("utf-8", errors="replace")
        doc_dir = posixpath.dirname(path)
        new_rel = quote(relative_href(doc_dir, new))
        pattern = CSS_URL_RE if item.get("media-type") == "text/css" else REF_ATTR_RE
        changed = False

        def repl(m, doc_dir=doc_dir, new_rel=new_rel, pattern=pattern):
            nonlocal changed
            val = m.group("val")
            if urlsplit(val).scheme or val.startswith("#"):
                return m.group(0)
            parts = urlsplit(val)
            if resolve_href(doc_dir, val) != old:
                return m.group(0)
            changed = True
            suffix = ("#" + parts.fragment) if parts.fragment else ""
            if pattern is CSS_URL_RE:
                return f"url({m.group('q')}{new_rel}{suffix}{m.group('q')})"
            return f"{m.group('attr')}{m.group('q')}{new_rel}{suffix}{m.group('q')}"

        new_text = pattern.sub(repl, text)
        if changed:
            out[path] = new_text.encode("utf-8")
    return out


# ---------------------------------------------------------------------------
# Navigation documents
# ---------------------------------------------------------------------------


def remove_nav_links(nav_bytes: bytes, nav_dir: str, target: str) -> tuple[bytes, int]:
    """Drop entries that link to target from an EPUB 3 nav document.

    A list item with nested entries keeps them: its link becomes a plain
    span so the children stay reachable.
    """
    root = load_xml(nav_bytes)
    removed = 0
    for a in list(root.iter(qn(XHTML_NS, "a"))):
        href = a.get("href", "")
        if urlsplit(href).scheme or resolve_href(nav_dir, href) != target:
            continue
        li = a.getparent()
        if (
            li is not None
            and li.tag == qn(XHTML_NS, "li")
            and li.find(qn(XHTML_NS, "ol")) is not None
        ):
            span = etree.Element(qn(XHTML_NS, "span"))
            span.text = "".join(a.itertext())
            span.tail = a.tail
            a.getparent().replace(a, span)
        elif li is not None and li.tag == qn(XHTML_NS, "li"):
            _remove_keep_tail(li)
        else:
            _remove_keep_tail(a)
        removed += 1
    # An <ol> left without <li> children is invalid in a nav document.
    for ol in list(root.iter(qn(XHTML_NS, "ol"))):
        if not any(isinstance(c.tag, str) and c.tag == qn(XHTML_NS, "li") for c in ol):
            parent = ol.getparent()
            if parent is not None and parent.tag == qn(XHTML_NS, "li"):
                _remove_keep_tail(ol)
    return dump_xml(root), removed


def remove_ncx_points(ncx_bytes: bytes, ncx_dir: str, target: str) -> tuple[bytes, int]:
    root = load_xml(ncx_bytes)
    removed = 0
    for content in list(root.iter(qn(NCX_NS, "content"))):
        src = content.get("src", "")
        if urlsplit(src).scheme or resolve_href(ncx_dir, src) != target:
            continue
        point = content.getparent()
        if point is not None and point.tag == qn(NCX_NS, "navPoint"):
            # Hoist nested navPoints so they are not lost with their parent.
            for child in list(point.findall(qn(NCX_NS, "navPoint"))):
                point.addprevious(child)
            _remove_keep_tail(point)
            removed += 1
    # Renumber playOrder so it stays sequential.
    for i, np in enumerate(root.iter(qn(NCX_NS, "navPoint")), 1):
        if np.get("playOrder") is not None:
            np.set("playOrder", str(i))
    return dump_xml(root), removed


GUIDE_TO_EPUB_TYPE = {
    "cover": "cover",
    "title-page": "titlepage",
    "toc": "toc",
    "index": "index",
    "glossary": "glossary",
    "acknowledgements": "acknowledgments",
    "bibliography": "bibliography",
    "colophon": "colophon",
    "copyright-page": "copyright-page",
    "dedication": "dedication",
    "epigraph": "epigraph",
    "foreword": "foreword",
    "loi": "loi",
    "lot": "lot",
    "notes": "endnotes",
    "preface": "preface",
    "text": "bodymatter",
}


def build_nav_document(
    root: etree._Element,
    base: str,
    zf: zipfile.ZipFile,
    nav_path: str,
    title: str,
    lang: str | None,
) -> tuple[bytes, list[str]]:
    """Generate an EPUB 3 navigation document from the NCX and the guide."""
    warnings: list[str] = []
    nav_dir = posixpath.dirname(nav_path)
    ncx_item = find_ncx_item(root)
    entries: list = []
    if ncx_item is not None:
        ncx_path = item_path(root, base, ncx_item)
        if ncx_path in zf.namelist():
            ncx = load_xml(zf.read(ncx_path))
            ncx_dir = posixpath.dirname(ncx_path)

            def walk(parent):
                out = []
                for np in parent.findall(qn(NCX_NS, "navPoint")):
                    label = np.find(f"{qn(NCX_NS, 'navLabel')}/{qn(NCX_NS, 'text')}")
                    content = np.find(qn(NCX_NS, "content"))
                    text = "".join(label.itertext()).strip() if label is not None else ""
                    href = None
                    if content is not None and content.get("src"):
                        src = content.get("src")
                        parts = urlsplit(src)
                        target = resolve_href(ncx_dir, src)
                        href = quote(relative_href(nav_dir, target))
                        if parts.fragment:
                            href += "#" + parts.fragment
                    out.append((text or "Untitled", href, walk(np)))
                return out

            nav_map = ncx.find(qn(NCX_NS, "navMap"))
            if nav_map is not None:
                entries = walk(nav_map)
        else:
            warnings.append(f"NCX {ncx_path} listed in the manifest is missing from the archive")
    if not entries:
        # Fall back to one entry per spine item so the nav is never empty.
        items = {i.get("id"): i for i in manifest_el(root).findall(OPF_ITEM)}
        for ref in spine_el(root).findall(OPF_ITEMREF):
            item = items.get(ref.get("idref"))
            if item is None:
                continue
            target = item_path(root, base, item)
            entries.append((posixpath.basename(target), quote(relative_href(nav_dir, target)), []))
        warnings.append("No NCX table of contents found; nav lists the spine documents instead")

    XH = "http://www.w3.org/1999/xhtml"
    html = etree.Element(qn(XH, "html"), nsmap={None: XH, "epub": EPUB_OPS_NS})
    if lang:
        html.set(qn(XML_NS, "lang"), lang)
        html.set("lang", lang)
    head = etree.SubElement(html, qn(XH, "head"))
    etree.SubElement(head, qn(XH, "meta")).set("charset", "utf-8")
    etree.SubElement(head, qn(XH, "title")).text = title
    body = etree.SubElement(html, qn(XH, "body"))
    nav = etree.SubElement(body, qn(XH, "nav"))
    nav.set(qn(EPUB_OPS_NS, "type"), "toc")
    nav.set("id", "toc")
    etree.SubElement(nav, qn(XH, "h1")).text = "Table of Contents"

    def emit(ol_parent, items):
        ol = etree.SubElement(ol_parent, qn(XH, "ol"))
        for text, href, children in items:
            li = etree.SubElement(ol, qn(XH, "li"))
            if href:
                a = etree.SubElement(li, qn(XH, "a"))
                a.set("href", href)
                a.text = text
            else:
                etree.SubElement(li, qn(XH, "span")).text = text
            if children:
                emit(li, children)

    emit(nav, entries)

    guide = root.find(qn(OPF_NS, "guide"))
    if guide is not None:
        refs = []
        for ref in guide.findall(OPF_REFERENCE):
            etype = GUIDE_TO_EPUB_TYPE.get(ref.get("type", ""))
            if not etype or not ref.get("href"):
                continue
            parts = urlsplit(ref.get("href"))
            target = resolve_href(base, ref.get("href"))
            href = quote(relative_href(nav_dir, target)) + (
                ("#" + parts.fragment) if parts.fragment else ""
            )
            refs.append((etype, href, ref.get("title") or etype))
        if refs:
            lm = etree.SubElement(body, qn(XH, "nav"))
            lm.set(qn(EPUB_OPS_NS, "type"), "landmarks")
            lm.set("hidden", "hidden")
            etree.SubElement(lm, qn(XH, "h2")).text = "Guide"
            ol = etree.SubElement(lm, qn(XH, "ol"))
            for etype, href, text in refs:
                li = etree.SubElement(ol, qn(XH, "li"))
                a = etree.SubElement(li, qn(XH, "a"))
                a.set(qn(EPUB_OPS_NS, "type"), etype)
                a.set("href", href)
                a.text = text

    etree.indent(html, space="  ")
    data = etree.tostring(html, xml_declaration=True, encoding="UTF-8", doctype="<!DOCTYPE html>")
    return data, warnings
