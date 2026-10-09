"""Build small, valid EPUB 2 and EPUB 3 files for the tests.

The fixtures are written from strings so the tests need neither pandoc nor
sample files checked into the repository. Both fixtures pass EPUBCheck
with no errors or warnings.
"""

from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

EPUB2_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <!-- publisher comment -->
    <dc:title>Old Book</dc:title>
    <dc:creator opf:role="aut" opf:file-as="Doe, John">John Doe</dc:creator>
    <dc:creator opf:role="ill">Ann Artist</dc:creator>
    <dc:identifier opf:scheme="ISBN">9781111111111</dc:identifier>
    <dc:identifier id="BookId" opf:scheme="UUID">urn:uuid:11111111-2222-3333-4444-555555555555</dc:identifier>
    <dc:language>en</dc:language>
    <dc:date opf:event="publication">2001-05-01</dc:date>
    <dc:date opf:event="creation">2000-12</dc:date>
    <dc:subject></dc:subject>
    <dc:subject>History</dc:subject>
    <meta name="cover" content="cover-img"/>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="cover-img" href="images/cover.jpg" media-type="image/jpeg"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
  </spine>
  <guide>
    <reference type="cover" title="Cover" href="ch1.xhtml#top"/>
    <reference type="text" title="Start" href="text/ch2.xhtml"/>
  </guide>
</package>
"""

EPUB2_NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:11111111-2222-3333-4444-555555555555"/>
    <meta name="dtb:depth" content="2"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>Old Book</text></docTitle>
  <navMap>
    <navPoint id="n1" playOrder="1">
      <navLabel><text>Chapter 1</text></navLabel>
      <content src="ch1.xhtml"/>
      <navPoint id="n1a" playOrder="2">
        <navLabel><text>Lead</text></navLabel>
        <content src="ch1.xhtml#lead"/>
      </navPoint>
    </navPoint>
    <navPoint id="n2" playOrder="3">
      <navLabel><text>Chapter 2</text></navLabel>
      <content src="text/ch2.xhtml"/>
    </navPoint>
  </navMap>
</ncx>
"""

EPUB2_CH1 = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Chapter 1</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <h1 id="top">Chapter 1</h1>
  <p id="lead" class="lead">A&nbsp;lead&mdash;paragraph.</p>
  <p><img src="images/cover.jpg" alt="cover"/></p>
</body>
</html>
"""

EPUB2_CH2 = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Chapter 2</title>
  <link rel="stylesheet" type="text/css" href="../style.css"/>
</head>
<body>
  <h1>Chapter 2</h1>
  <p>Second chapter. <a href="../ch1.xhtml#lead">Back</a></p>
</body>
</html>
"""

CSS = "p.lead { font-variant: small-caps; }\nbody { background: url(images/cover.jpg); }\n"

EPUB3_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" xml:lang="en-US" unique-identifier="pub-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="pub-id">urn:isbn:9780000000001</dc:identifier>
    <dc:title id="title">New Book</dc:title>
    <meta refines="#title" property="title-type">main</meta>
    <dc:language>en-US</dc:language>
    <dc:creator id="creator-1">Jane Doe</dc:creator>
    <meta refines="#creator-1" property="role" scheme="marc:relators">aut</meta>
    <meta refines="#creator-1" property="file-as">Doe, Jane</meta>
    <dc:creator id="creator-2">John Roe</dc:creator>
    <meta refines="#creator-2" property="role" scheme="marc:relators">aut</meta>
    <dc:subject>Fiction</dc:subject>
    <dc:subject>Adventure</dc:subject>
    <dc:date>2026-01-01</dc:date>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
    <meta property="schema:accessMode">textual</meta>
    <meta property="schema:accessibilityFeature">structuralNavigation</meta>
    <meta property="schema:accessibilityFeature">tableOfContents</meta>
    <meta property="schema:accessibilityHazard">none</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="cover-img" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
  </spine>
</package>
"""

EPUB3_NAV = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body>
  <nav epub:type="toc">
    <h1>Contents</h1>
    <ol>
      <li><a href="ch1.xhtml">Chapter 1</a>
        <ol><li><a href="ch1.xhtml#lead">Lead</a></li></ol>
      </li>
      <li><a href="text/ch2.xhtml">Chapter 2</a></li>
    </ol>
  </nav>
</body>
</html>
"""

EPUB3_NCX = EPUB2_NCX.replace(
    "urn:uuid:11111111-2222-3333-4444-555555555555", "urn:isbn:9780000000001"
).replace("Old Book", "New Book")

EPUB3_CH1 = (
    EPUB2_CH1.replace(
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">',
        "<!DOCTYPE html>",
    )
    .replace("&nbsp;", "&#160;")
    .replace("&mdash;", "&#8212;")
)
EPUB3_CH2 = EPUB2_CH2.replace(
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">',
    "<!DOCTYPE html>",
)


def tiny_jpeg() -> bytes:
    """Smallest JPEG that image tools accept (a 1x1 grey pixel)."""
    return bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbd0ffd9"
    )


def tiny_png() -> bytes:
    """A 2x2 red PNG built by hand."""
    w = h = 2
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _write(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in entries.items():
            zf.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return path


def make_epub2(path: Path) -> Path:
    return _write(
        path,
        {
            "META-INF/container.xml": CONTAINER.encode(),
            "OEBPS/content.opf": EPUB2_OPF.encode(),
            "OEBPS/toc.ncx": EPUB2_NCX.encode(),
            "OEBPS/style.css": CSS.encode(),
            "OEBPS/images/cover.jpg": tiny_jpeg(),
            "OEBPS/ch1.xhtml": EPUB2_CH1.encode(),
            "OEBPS/text/ch2.xhtml": EPUB2_CH2.encode(),
        },
    )


def make_epub3(path: Path, cover: bool = True) -> Path:
    """EPUB 3 fixture; with cover=False nothing in the book mentions a cover."""
    entries = {
        "META-INF/container.xml": CONTAINER.encode(),
        "OEBPS/content.opf": EPUB3_OPF.encode(),
        "OEBPS/nav.xhtml": EPUB3_NAV.encode(),
        "OEBPS/toc.ncx": EPUB3_NCX.encode(),
        "OEBPS/style.css": CSS.encode(),
        "OEBPS/images/cover.jpg": tiny_jpeg(),
        "OEBPS/ch1.xhtml": EPUB3_CH1.encode(),
        "OEBPS/text/ch2.xhtml": EPUB3_CH2.encode(),
    }
    if not cover:
        del entries["OEBPS/images/cover.jpg"]
        entries["OEBPS/content.opf"] = (
            "\n".join(line for line in EPUB3_OPF.splitlines() if "cover" not in line).encode()
            + b"\n"
        )
        entries["OEBPS/style.css"] = CSS.splitlines()[0].encode() + b"\n"
        entries["OEBPS/ch1.xhtml"] = (
            "\n".join(line for line in EPUB3_CH1.splitlines() if "cover.jpg" not in line).encode()
            + b"\n"
        )
    return _write(path, entries)
