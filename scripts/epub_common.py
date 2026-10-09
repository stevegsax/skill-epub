"""Shared helpers for the EPUB skill scripts.

This module is imported by epub_metadata.py, epub_update.py, and
epub_upgrade.py. It has no third-party dependencies, so any script that
imports it only needs the dependencies it declares in its own PEP 723
header.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

# XML namespaces used in EPUB files.
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_OPS_NS = "http://www.idpf.org/2007/ops"
XML_NS = "http://www.w3.org/XML/1998/namespace"

NS = {
    "container": CONTAINER_NS,
    "opf": OPF_NS,
    "dc": DC_NS,
    "dcterms": DCTERMS_NS,
    "ncx": NCX_NS,
    "xhtml": XHTML_NS,
    "epub": EPUB_OPS_NS,
}

# Media types that EPUBCheck and reading systems expect for common files.
# Used when the Python mimetypes module gives no answer or a wrong one.
MEDIA_TYPES = {
    ".xhtml": "application/xhtml+xml",
    ".html": "application/xhtml+xml",
    ".htm": "application/xhtml+xml",
    ".css": "text/css",
    ".js": "text/javascript",
    ".ncx": "application/x-dtbncx+xml",
    ".opf": "application/oebps-package+xml",
    ".smil": "application/smil+xml",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".otf": "font/otf",
    ".ttf": "font/ttf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".json": "application/json",
    ".xml": "application/xml",
    ".txt": "text/plain",
    ".pls": "application/pls+xml",
    ".vtt": "text/vtt",
}

# dc:date and dcterms:modified values must be ISO 8601 / W3CDTF.
# EPUB 3.3 requires dcterms:modified to be the full CCYY-MM-DDThh:mm:ssZ
# form; dc:date accepts any precision from a bare year down to a full
# UTC timestamp. These two patterns encode exactly those shapes.
ISO_DATE_RE = re.compile(
    r"^\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01])"
    r"(T([01]\d|2[0-3]):[0-5]\d(:[0-5]\d(\.\d+)?)?(Z|[+-]([01]\d|2[0-3]):[0-5]\d))?)?)?$"
)
MODIFIED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class EpubError(Exception):
    """Raised for problems that make the requested operation impossible."""


def is_iso_date(value: str) -> bool:
    """Return True when value is an ISO 8601 date EPUB accepts in dc:date."""
    return bool(ISO_DATE_RE.match(value.strip()))


def is_modified_timestamp(value: str) -> bool:
    """Return True when value has the exact form dcterms:modified requires."""
    return bool(MODIFIED_RE.match(value.strip()))


def now_utc_iso() -> str:
    """Current UTC time in the form dcterms:modified requires."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_opf_path(zf: zipfile.ZipFile) -> str:
    """Locate the package document path from META-INF/container.xml."""
    try:
        container_xml = zf.read("META-INF/container.xml")
    except KeyError:
        raise EpubError("Not a valid EPUB: missing META-INF/container.xml") from None
    try:
        root = ET.fromstring(container_xml)
    except ET.ParseError as e:
        raise EpubError(f"META-INF/container.xml is not well-formed XML: {e}") from None
    rootfile = root.find(".//container:rootfile", NS)
    if rootfile is None or not rootfile.get("full-path"):
        raise EpubError("No rootfile with a full-path found in container.xml")
    path = rootfile.get("full-path")
    if path not in zf.namelist():
        raise EpubError(f"container.xml points to {path}, which is not in the archive")
    return path


def opf_dir(opf_path: str) -> str:
    """Directory of the package document inside the archive ('' at root)."""
    d = posixpath.dirname(opf_path)
    return d


def resolve_href(base_dir: str, href: str) -> str:
    """Turn an href relative to base_dir into a normalized archive path.

    Strips any fragment and query, decodes percent-escapes, and collapses
    '.' and '..' segments, so 'text/../images/a%20b.png#frag' under
    'OEBPS' becomes 'OEBPS/images/a b.png'.
    """
    parts = urlsplit(href)
    path = unquote(parts.path)
    if not path:
        return base_dir
    joined = posixpath.join(base_dir, path) if base_dir else path
    return posixpath.normpath(joined)


def relative_href(from_dir: str, target_path: str) -> str:
    """Relative href from a directory to an archive path, POSIX style."""
    return posixpath.relpath(target_path, start=from_dir or ".")


def split_properties(value: str | None) -> list[str]:
    """Split a whitespace-separated properties attribute into tokens."""
    return value.split() if value else []


def guess_media_type(path: str) -> str | None:
    """Media type for a file name, preferring the EPUB core media types."""
    ext = posixpath.splitext(path.lower())[1]
    if ext in MEDIA_TYPES:
        return MEDIA_TYPES[ext]
    import mimetypes

    guessed, _ = mimetypes.guess_type(path)
    return guessed


def write_epub(
    source: str,
    output: str,
    replacements: dict[str, bytes],
    removals: set[str],
) -> None:
    """Write a copy of the EPUB at source to output with changes applied.

    Entries named in replacements are written from the given bytes (added if
    new); entries in removals are left out. All other entries are copied
    with their original compression. The mimetype entry is always first and
    stored uncompressed, as the OCF specification requires. The output is
    written to a temporary file next to the destination and renamed into
    place, so a failure never leaves a half-written EPUB behind.
    """
    import os
    import shutil
    import tempfile

    out_dir = os.path.dirname(os.path.abspath(output)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".epub-", suffix=".tmp", dir=out_dir)
    os.close(fd)
    try:
        with zipfile.ZipFile(source, "r") as zin, zipfile.ZipFile(tmp_path, "w") as zout:
            names = zin.namelist()
            mimetype = replacements.get("mimetype")
            if mimetype is None:
                mimetype = zin.read("mimetype") if "mimetype" in names else b"application/epub+zip"
            zout.writestr("mimetype", mimetype, compress_type=zipfile.ZIP_STORED)

            skip = set(replacements) | removals | {"mimetype"}
            for info in zin.infolist():
                if info.filename in skip or info.filename.endswith("/"):
                    continue
                zout.writestr(info, zin.read(info.filename))

            for path, data in replacements.items():
                if path == "mimetype":
                    continue
                zout.writestr(path, data, compress_type=zipfile.ZIP_DEFLATED)
        shutil.move(tmp_path, output)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def check_output_path(input_path: str, output_path: str) -> None:
    """Refuse to write the output over the input."""
    import os

    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise EpubError(
            "--output must differ from the input file; the scripts never modify an EPUB in place"
        )
