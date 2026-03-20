# EPUB 3 File Structure Reference

This reference covers the EPUB 3 format (current W3C standard: EPUB 3.3). EPUB files are ZIP
archives with the `.epub` extension. The ZIP must contain a `mimetype` file as its first entry,
stored without compression, containing exactly `application/epub+zip`.

## Directory Layout

A typical EPUB 3 file contains:

```
book.epub
├── mimetype
├── META-INF/
│   └── container.xml
├── OEBPS/                    (or "EPUB/", "content/", etc.)
│   ├── content.opf           (package document — version="3.0")
│   ├── toc.xhtml             (EPUB 3 navigation document — required)
│   ├── toc.ncx               (legacy NCX — optional, for backward compatibility)
│   ├── stylesheet.css
│   ├── images/
│   │   ├── cover.jpg
│   │   └── figure1.png
│   ├── chapter01.xhtml
│   ├── chapter02.xhtml
│   └── ...
└── (no other top-level files)
```

The content directory name (`OEBPS`, `EPUB`, etc.) is not fixed — it is referenced by
`META-INF/container.xml`.

## META-INF/container.xml

Points to the package document (OPF file):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
              media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
```

## Package Document (OPF)

The OPF file is the central manifest. The root `<package>` element must have `version="3.0"`.

```xml
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
```

It has three main sections:

### Metadata

Dublin Core metadata describes the publication. EPUB 3 requires `dc:identifier`, `dc:title`,
`dc:language`, and a `<meta property="dcterms:modified">` timestamp.

```xml
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:identifier id="uid">urn:uuid:12345678-1234-1234-1234-123456789012</dc:identifier>
  <dc:title>Book Title</dc:title>
  <dc:creator>Author Name</dc:creator>
  <dc:language>en</dc:language>
  <dc:date>2026-01-01</dc:date>
  <dc:publisher>Publisher Name</dc:publisher>
  <dc:description>A description of the book.</dc:description>
  <dc:rights>All rights reserved.</dc:rights>
  <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
</metadata>
```

EPUB 3 uses `<meta property="...">` elements instead of the EPUB 2-style `<meta name="..."
content="..."/>` for custom metadata. The `dcterms:modified` meta is required by the spec.

### Manifest

Lists every file in the publication. EPUB 3 uses the `properties` attribute for semantic roles:

```xml
<manifest>
  <item id="nav" href="toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  <item id="style" href="stylesheet.css" media-type="text/css"/>
  <item id="cover-image" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>
  <item id="ch01" href="chapter01.xhtml" media-type="application/xhtml+xml"/>
  <item id="ch02" href="chapter02.xhtml" media-type="application/xhtml+xml"/>
</manifest>
```

Every file used in the EPUB must appear in the manifest with a unique `id`, relative `href`, and
correct `media-type`.

Key EPUB 3 manifest properties:

- `nav` — the EPUB 3 navigation document (exactly one required)
- `cover-image` — the cover image
- `mathml` — content contains MathML
- `svg` — content contains SVG
- `scripted` — content contains JavaScript

### Spine

Defines the default reading order:

```xml
<spine>
  <itemref idref="ch01"/>
  <itemref idref="ch02"/>
</spine>
```

Each `itemref` references an `id` from the manifest. The `toc` attribute on `<spine>` (pointing
to an NCX) is optional in EPUB 3 — it exists only for backward compatibility with EPUB 2
reading systems.

## Navigation Document (Required in EPUB 3)

The navigation document is an XHTML5 file declared with `properties="nav"` in the manifest. It
replaces the EPUB 2 NCX as the required navigation mechanism.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Table of Contents</title></head>
<body>
  <nav epub:type="toc">
    <h1>Table of Contents</h1>
    <ol>
      <li><a href="chapter01.xhtml">Chapter 1: Introduction</a></li>
      <li><a href="chapter02.xhtml">Chapter 2: Getting Started</a>
        <ol>
          <li><a href="chapter02.xhtml#setup">Setup</a></li>
        </ol>
      </li>
    </ol>
  </nav>
</body>
</html>
```

The `epub:type="toc"` nav element is required. Optional nav types include `landmarks`
and `page-list`.

## Content Documents (XHTML5)

EPUB 3 content documents use XHTML5 (HTML5 serialized as XML). Must be well-formed XML:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Chapter 1</title>
  <link rel="stylesheet" type="text/css" href="stylesheet.css"/>
</head>
<body>
  <h1>Chapter 1: Introduction</h1>
  <p>Content goes here.</p>
  <img src="images/figure1.png" alt="Figure 1: Description"/>
</body>
</html>
```

Key requirements:

- All tags must be closed (`<br/>` not `<br>`)
- All attributes must be quoted
- Images must have `alt` attributes
- The `xmlns` declaration is required on the `<html>` element
- CSS3 is supported (EPUB 2 was limited to a CSS 2.1 subset)
- Audio (`<audio>`), video (`<video>`), MathML, and inline SVG are supported
- JavaScript is allowed but must be declared via `properties="scripted"` in the manifest

## Identifying EPUB 2 Files (for Conversion)

When encountering an existing EPUB, check the version in the OPF `<package>` element:

- `version="2.0"` — EPUB 2. Convert to EPUB 3.
- `version="3.0"` — EPUB 3. No conversion needed.

Structural differences between EPUB 2 and EPUB 3:

| Feature | EPUB 2 (legacy) | EPUB 3 (current) |
|---|---|---|
| OPF version | `"2.0"` | `"3.0"` |
| Content format | XHTML 1.1 | XHTML5 |
| Navigation | NCX (required) | XHTML nav (required), NCX (optional) |
| Modified timestamp | Not required | `<meta property="dcterms:modified">` required |
| Media types | Limited | Audio, video, MathML, SVG |
| Scripting | Not supported | JavaScript (with `scripted` property) |
| Styling | CSS 2.1 subset | CSS3 |
| Metadata | Dublin Core + `<meta name="">` | Dublin Core + `<meta property="">` |
| Cover image | `<meta name="cover" content="id">` | `properties="cover-image"` on manifest item |

## Common Media Types

| Extension | Media Type |
|---|---|
| `.xhtml` | `application/xhtml+xml` |
| `.css` | `text/css` |
| `.jpg`, `.jpeg` | `image/jpeg` |
| `.png` | `image/png` |
| `.gif` | `image/gif` |
| `.svg` | `image/svg+xml` |
| `.ncx` | `application/x-dtbncx+xml` |
| `.otf` | `font/otf` |
| `.ttf` | `font/ttf` |
| `.woff` | `font/woff` |
| `.woff2` | `font/woff2` |
| `.js` | `text/javascript` |
| `.mp3` | `audio/mpeg` |
| `.mp4` | `video/mp4` |

## Repackaging Rules

When rebuilding an EPUB from extracted files:

1. The `mimetype` file must be the first entry in the ZIP archive
2. The `mimetype` file must be stored without compression (`ZIP_STORED`)
3. The `mimetype` file must not have an extra field in its ZIP header (`-X` flag)
4. All other files should use deflate compression

```bash
# Correct repackaging command
zip -X0 book.epub mimetype
zip -Xr9D book.epub * -x mimetype
```
