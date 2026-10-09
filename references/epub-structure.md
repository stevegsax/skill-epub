# EPUB 3 File Structure Reference

Contents: [Container layout](#container-layout) · [container.xml](#meta-infcontainerxml) ·
[Package document](#package-document-opf) · [Navigation document](#navigation-document) ·
[Content documents](#content-documents-xhtml5) · [EPUB 2 differences](#epub-2-and-epub-3) ·
[Media types](#core-media-types) · [Accessibility metadata](#accessibility-metadata) ·
[Packing rules](#packing-rules)

This reference covers EPUB 3.3, the current W3C standard. An EPUB is a ZIP archive with the
`.epub` extension. The first entry must be a file named `mimetype`, stored without
compression, containing exactly `application/epub+zip`. The only other reserved name is the
`META-INF` directory; everything else can be laid out as the producer likes.

## Container layout

A typical EPUB 3 file:

```text
book.epub
├── mimetype
├── META-INF/
│   └── container.xml
└── OEBPS/                    (pandoc uses "EPUB/"; any name works)
    ├── content.opf           package document, version="3.0"
    ├── nav.xhtml             EPUB 3 navigation document (required)
    ├── toc.ncx               EPUB 2 NCX (optional, for old reading systems)
    ├── stylesheet.css
    ├── images/cover.jpg
    ├── chapter01.xhtml
    └── chapter02.xhtml
```

The content directory name is not fixed; `META-INF/container.xml` says where the package
document is, and every other path is relative to that document.

## META-INF/container.xml

```xml
<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
```

## Package document (OPF)

The root `<package>` element must have `version="3.0"` (EPUB 3.3 keeps the `3.0` value) and
a `unique-identifier` attribute naming the id of the `dc:identifier` that identifies the
publication.

```xml
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">
```

### Metadata

Required: one `dc:identifier` (the one `unique-identifier` points at), at least one
`dc:title`, at least one `dc:language`, and exactly one `<meta property="dcterms:modified">`
whose value has the form `CCYY-MM-DDThh:mm:ssZ`. `dc:date` is optional, holds the
publication date, and should be ISO 8601 (`2026`, `2026-01`, or `2026-01-31`). Empty Dublin
Core elements are errors.

```xml
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:identifier id="pub-id">urn:isbn:9780000000000</dc:identifier>
  <dc:title>Book Title</dc:title>
  <dc:creator id="creator-1">Author Name</dc:creator>
  <meta refines="#creator-1" property="role" scheme="marc:relators">aut</meta>
  <meta refines="#creator-1" property="file-as">Name, Author</meta>
  <dc:language>en</dc:language>
  <dc:date>2026-01-01</dc:date>
  <dc:publisher>Publisher Name</dc:publisher>
  <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
</metadata>
```

Extra information about an element goes in a `<meta refines="#id" property="...">` element.
Unprefixed property names come from the package metadata vocabulary (`role`, `file-as`,
`title-type`, `identifier-type`, `display-seq`, `group-position`). Prefixed names use one
of the reserved prefixes, usable without declaration: `dcterms:`, `marc:`, `media:`,
`onix:`, `rendition:`, `schema:`, `a11y:`, `xsd:`. Any other prefix must be declared in the
package element's `prefix` attribute. The EPUB 2 `<meta name="cover" content="id"/>` form
is still accepted for backward compatibility.

### Manifest

Every file in the publication except `mimetype` and the `META-INF` files is listed once,
with a unique `id`, an `href` relative to the package document, and the right `media-type`.
The `properties` attribute marks special roles: `nav` (exactly one item), `cover-image`,
`mathml`, `svg`, `scripted`, `remote-resources`.

```xml
<manifest>
  <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  <item id="css" href="stylesheet.css" media-type="text/css"/>
  <item id="cover" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>
  <item id="ch01" href="chapter01.xhtml" media-type="application/xhtml+xml"/>
</manifest>
```

A file in the archive that the manifest does not list is reported by EPUBCheck; a manifest
item whose file is missing is an error.

### Spine

The default reading order. Each `itemref` names a manifest `id`; `linear="no"` marks
documents that are reachable only by links. The `toc` attribute, pointing at an NCX item,
exists only for EPUB 2 reading systems.

```xml
<spine toc="ncx">
  <itemref idref="ch01"/>
  <itemref idref="ch02"/>
</spine>
```

The `<guide>` element is deprecated in EPUB 3 but still accepted; its role is taken by the
`landmarks` nav in the navigation document.

## Navigation document

An XHTML file declared with `properties="nav"`. It must contain a `<nav epub:type="toc">`
with an ordered list; `landmarks` and `page-list` navs are optional. It may also appear in
the spine if the table of contents should be a readable page.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body>
  <nav epub:type="toc">
    <h1>Contents</h1>
    <ol>
      <li><a href="chapter01.xhtml">Chapter 1</a></li>
      <li><a href="chapter02.xhtml">Chapter 2</a>
        <ol><li><a href="chapter02.xhtml#setup">Setup</a></li></ol>
      </li>
    </ol>
  </nav>
  <nav epub:type="landmarks" hidden="hidden">
    <ol><li><a epub:type="bodymatter" href="chapter01.xhtml">Start of content</a></li></ol>
  </nav>
</body>
</html>
```

Every `<li>` holds an `<a>` (with an `href`) or a `<span>` (a heading with no link), followed
by an optional nested `<ol>`. An empty `<ol>` is an error, and so is a link to a fragment that
does not exist.

## Content documents (XHTML5)

Content is XHTML5: HTML5 vocabulary serialized as well-formed XML.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
  <title>Chapter 1</title>
  <link rel="stylesheet" type="text/css" href="stylesheet.css"/>
</head>
<body epub:type="bodymatter">
  <section epub:type="chapter">
    <h1>Chapter 1</h1>
    <p>Content goes here.</p>
    <img src="images/figure1.png" alt="Figure 1: what it shows"/>
  </section>
</body>
</html>
```

Requirements EPUBCheck enforces: the DOCTYPE is `<!DOCTYPE html>` or absent (an XHTML 1.1
DOCTYPE is `HTM-004`); every tag is closed and every attribute quoted; named character
references other than the five XML ones (`&amp;` `&lt;` `&gt;` `&quot;` `&apos;`) are
undefined without a DTD, so write `&#160;` rather than `&nbsp;`; `<img>` has an `alt`
attribute; the `xmlns` declaration is on `<html>`; presentational HTML 4 attributes and
elements (`align`, `bgcolor`, `<center>`, `<font>`) are not allowed, so use CSS. Scripts are
allowed when the manifest item carries `properties="scripted"`. MathML, SVG, audio, and video
are allowed inline.

## EPUB 2 and EPUB 3

Check `version` on the `<package>` element: `2.0` is EPUB 2, `3.0` is EPUB 3.

| Feature | EPUB 2 | EPUB 3 |
| --- | --- | --- |
| Package version | `2.0` | `3.0` |
| Content | XHTML 1.1 | XHTML5 |
| Navigation | NCX, required | XHTML nav document, required; NCX optional |
| Modified timestamp | none | `dcterms:modified` required |
| Creator role, sort form | `opf:role`, `opf:file-as` attributes | `<meta refines>` with `role` (scheme `marc:relators`) and `file-as` |
| Identifier scheme | `opf:scheme` attribute | `<meta refines>` with `identifier-type` |
| Date events | `opf:event` on several `dc:date` | one `dc:date` (publication); `dcterms:created`, `dcterms:issued` metas for others |
| Cover image | `<meta name="cover" content="id"/>` | `properties="cover-image"` on the item |
| Custom metadata | `<meta name="" content=""/>` | `<meta property="">` |
| Styling | CSS 2.1 subset | CSS 3 |
| Media | images only | audio, video, MathML, SVG, scripting |

The `opf:` attributes are not allowed in an EPUB 3 package at all; EPUBCheck reports each one
as `RSC-005`. `epub_upgrade.py` performs the conversions in this table.

## Core media types

A file whose type is in this list needs no fallback. Other types (`application/pdf`,
`text/html`, `audio/wav`) must have a manifest `fallback` to a core type.

| Kind | Media types |
| --- | --- |
| Content | `application/xhtml+xml`, `image/svg+xml` |
| Images | `image/jpeg`, `image/png`, `image/gif`, `image/webp`, `image/svg+xml` |
| Fonts | `font/ttf`, `font/otf`, `font/woff`, `font/woff2` |
| Audio | `audio/mpeg`, `audio/mp4`, `audio/ogg; codecs=opus` |
| Style and script | `text/css`, `text/javascript` |
| Other | `application/x-dtbncx+xml` (NCX), `application/smil+xml` (media overlays), `application/pls+xml`, `text/vtt` |

Video has no core media type; `video/mp4` and `video/webm` are widely supported but need a
fallback to validate without a warning.

## Accessibility metadata

Set these `<meta property>` values on the publication (no `refines`):

| Property | Values |
| --- | --- |
| `schema:accessMode` | `textual`, `visual`, `auditory` (one element per mode) |
| `schema:accessModeSufficient` | comma-separated modes that suffice, e.g. `textual` |
| `schema:accessibilityFeature` | `structuralNavigation`, `tableOfContents`, `readingOrder`, `alternativeText`, `displayTransformability`, `printPageNumbers`, `MathML` |
| `schema:accessibilityHazard` | `none`, or `flashing`, `motionSimulation`, `sound` |
| `schema:accessibilitySummary` | a sentence for readers describing the accessibility of the book |
| `dcterms:conformsTo` | `EPUB-A11Y-11_WCAG-21-AA` (or `-A`, `-AAA`; also WCAG 2.0/2.2 forms) |
| `a11y:certifiedBy` | who checked the book |

EPUBCheck validates the vocabulary; the DAISY Ace checker evaluates the book.

## Packing rules

When rebuilding an EPUB from extracted files:

1. `mimetype` is the first entry, stored uncompressed, with no extra field in its header.
2. Everything else is deflate-compressed.
3. Paths use forward slashes and are case-sensitive; `href` values must be percent-encoded.

```bash
cd unpacked_book
zip -X0 ../book.epub mimetype
zip -Xr9D ../book.epub META-INF OEBPS
```

`epub_update.py --add` and `--remove` do this packing for single files and keep the
manifest in step, so prefer them over manual repacking when only a few files change.
