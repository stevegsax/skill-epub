---
name: epub
description: >
  Use local CLI tools to create, validate, query, update, and convert EPUB files. All output
  targets EPUB 3 (the current W3C standard, EPUB 3.3). Use when the user mentions EPUB, e-book,
  ebook, "create an epub", "validate epub", "check epub", "epub metadata", "convert to epub",
  "convert from epub", "update epub", "modify epub", "inspect epub", "epub table of contents",
  "epub cover", "upgrade epub", "epub version", "epub 3", or any task involving .epub files.
  Also trigger when the user wants to package HTML or Markdown content as an e-book, extract
  content from an EPUB, fix EPUB validation errors, or convert an older EPUB 2 file to EPUB 3.
  Do NOT use for PDF-only workflows, Kindle-only formats (MOBI/AZW3), or general file conversion
  that does not involve EPUB.
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
---

# EPUB Skill

Work with EPUB files using local command-line tools. All operations target **EPUB 3** (the
current W3C standard, EPUB 3.3). EPUB files are ZIP archives containing XHTML5 content,
metadata, and navigation documents.

When working with older EPUB 2 files, convert them to EPUB 3 — see the conversion section below.

## Prerequisites

Check tool availability before proceeding. Run `epubcheck --help` and `pandoc --help` to
confirm available options — these tools evolve and their capabilities should be verified rather
than assumed.

- **pandoc** — create and convert EPUBs (required; install via `brew install pandoc`).
  Supports three EPUB output formats: `epub` (defaults to EPUB 3), `epub2`, and `epub3`.
  Always use `-t epub3` explicitly to ensure EPUB 3 output.
- **epubcheck** — validate EPUB conformance (recommended; install via `brew install epubcheck`).
  Validates against EPUB 3 by default. Supports JSON output (`--json`), profile-based
  validation (`--profile`), and individual file validation (`--mode`).
- **unzip / zipinfo** — inspect EPUB internals (built-in on macOS)
- **Python 3** — run helper scripts for metadata extraction and modification

If a required tool is missing, inform the user and provide the install command.

## EPUB 3 Requirements

EPUB 3 content must meet these requirements (epubcheck enforces all of them):

- **Package document (OPF)** version attribute must be `"3.0"`
- **Navigation document** — an XHTML file with `properties="nav"` in the manifest, containing
  a `<nav epub:type="toc">` element. This replaces the EPUB 2 NCX as the required navigation
  mechanism (NCX may be included for backward compatibility but is not required).
- **Content documents** use XHTML5 (not XHTML 1.1), with `xmlns="http://www.w3.org/1999/xhtml"`
- **Required metadata** — `dc:identifier`, `dc:title`, `dc:language`, and a
  `<meta property="dcterms:modified">` timestamp
- **Manifest** items may use `properties` attribute for semantic roles (`nav`, `cover-image`,
  `mathml`, `svg`, `scripted`, etc.)

Read `references/epub-structure.md` for the full EPUB 3 structure specification.

## Core Operations

### 1. Create an EPUB 3

Use pandoc with `-t epub3` to create EPUB 3 files from Markdown, HTML, or other supported formats.

```bash
# Basic creation from Markdown
pandoc input.md -t epub3 -o output.epub

# With metadata
pandoc input.md -t epub3 -o output.epub \
  --metadata title="Book Title" \
  --metadata author="Author Name" \
  --metadata lang="en-US"

# With cover image, CSS, and table of contents
# --toc generates an EPUB 3 nav document from headings in the source
# --toc-depth controls how many heading levels appear (default: 3)
pandoc input.md -t epub3 -o output.epub \
  --epub-cover-image=cover.jpg \
  --css=style.css \
  --toc --toc-depth=2

# From multiple source files (chapters)
# --epub-chapter-level=N splits output into separate XHTML files at heading level N
# --top-level-division=chapter treats top-level headings as chapters (vs. sections or parts)
pandoc ch01.md ch02.md ch03.md -t epub3 -o output.epub \
  --metadata-file=metadata.yaml \
  --epub-cover-image=cover.jpg \
  --toc \
  --epub-chapter-level=1 \
  --top-level-division=chapter
```

For structured books, use a YAML metadata file:

```yaml
---
title: Book Title
author: Author Name
date: 2026-01-01
lang: en-US
rights: All rights reserved
description: A brief description of the book.
---
```

Pass it with `--metadata-file=metadata.yaml`.

### 2. Validate an EPUB

Use epubcheck to validate against the EPUB 3 specification:

```bash
# Full validation (EPUB 3 by default)
epubcheck book.epub

# Machine-readable JSON output for programmatic analysis
epubcheck book.epub --json results.json

# Validate with usage information (shows EPUB features used)
epubcheck book.epub --usage

# Fail on warnings too (stricter validation)
epubcheck book.epub --failonwarnings
```

Always validate after creating or modifying an EPUB. When fixing validation errors, parse the
output systematically — fix errors before warnings, and re-validate after each round of fixes.

Common EPUB 3 validation issues:

- **Missing `dcterms:modified`** — Add `<meta property="dcterms:modified">` to metadata
- **Missing nav document** — Ensure an XHTML file with `properties="nav"` exists in the manifest
- **Missing alt text** — Add `alt` attributes to all `<img>` tags
- **Invalid XHTML5** — Ensure all tags are properly closed and content is well-formed XML
- **Missing required metadata** — Add `dc:identifier`, `dc:title`, `dc:language` to the OPF

### 3. Query EPUB Contents

EPUBs are ZIP archives. Inspect them directly:

```bash
# List all files in the EPUB
zipinfo -1 book.epub

# Extract the OPF (package) file to read metadata and check EPUB version
unzip -p book.epub OEBPS/content.opf 2>/dev/null || unzip -p book.epub content.opf 2>/dev/null

# Extract the EPUB 3 navigation document
unzip -p book.epub OEBPS/toc.xhtml 2>/dev/null

# Extract a specific chapter
unzip -p book.epub OEBPS/ch01.xhtml
```

For structured metadata extraction (including version detection), use the helper script:

```bash
python3 scripts/epub_metadata.py book.epub
```

The script reports the EPUB version, which is useful for identifying files that need conversion
to EPUB 3. Add `--json` for machine-readable JSON-LD output with Dublin Core vocabulary (`dc:`
and `dcterms:` terms):

```bash
python3 scripts/epub_metadata.py book.epub --json
```

Use pandoc to convert an EPUB back to readable text:

```bash
# Extract as Markdown
pandoc book.epub -t markdown -o output.md

# Extract as plain text
pandoc book.epub -t plain -o output.txt

# Extract as HTML
pandoc book.epub -t html -o output.html
```

### 4. Update an EPUB

EPUBs can be modified by extracting, editing, and repackaging:

```bash
# Use the helper script for metadata updates
python3 scripts/epub_update.py book.epub --title "New Title" --author "New Author"

# Round-trip: extract metadata as JSON-LD, edit, and apply back
python3 scripts/epub_metadata.py book.epub --json > meta.json
# ... edit meta.json ...
python3 scripts/epub_update.py book.epub --metadata-file meta.json

# CLI flags override file values
python3 scripts/epub_update.py book.epub --metadata-file meta.json --title "Override Title"

# Manual approach: extract, edit, repack
mkdir -p epub_work && cd epub_work
unzip ../book.epub
# ... edit files ...
# Repack (mimetype must be first, uncompressed)
zip -X0 ../updated.epub mimetype
zip -Xr9D ../updated.epub * -x mimetype
```

The repackaging order matters — `mimetype` must be the first entry and stored without compression.

### 5. Convert To/From EPUB 3

Use pandoc for format conversion. Always use `-t epub3` when outputting EPUB.

```bash
# EPUB to other formats
pandoc book.epub -o book.pdf
pandoc book.epub -o book.docx
pandoc book.epub -o book.html

# Other formats to EPUB 3
pandoc document.docx -t epub3 -o book.epub
pandoc page.html -t epub3 -o book.epub
pandoc paper.tex -t epub3 -o book.epub
```

### 6. Convert EPUB 2 to EPUB 3

Pandoc reads EPUB 2 files natively and can output EPUB 3, handling the structural conversion
automatically — upgrading the navigation from NCX to an XHTML nav document, converting content
from XHTML 1.1 to XHTML5, and adding the required EPUB 3 metadata.

```bash
# Convert EPUB 2 to EPUB 3 (always work on a copy)
pandoc old_book.epub -t epub3 -o new_book.epub

# Regenerate the EPUB 3 nav document from headings (useful if the original TOC is sparse)
pandoc old_book.epub -t epub3 -o new_book.epub \
  --toc --toc-depth=2

# Override metadata during conversion
pandoc old_book.epub -t epub3 -o new_book.epub \
  --metadata title="Updated Title" \
  --metadata lang="en-US"
```

After conversion, always validate the result:

```bash
epubcheck new_book.epub
```

If epubcheck reports errors, fix them and re-validate. Common post-conversion issues:

- **Deprecated attributes** — Remove EPUB 2-era attributes that are not valid in EPUB 3
  (e.g., `align` on elements — use CSS instead)
- **NCX-only navigation** — Pandoc should generate the EPUB 3 nav document automatically,
  but verify it exists in the output (`zipinfo -1 new_book.epub | grep -i nav`)
- **Missing `dcterms:modified`** — Pandoc adds this, but if updating metadata manually,
  ensure it is present

For batch conversion of multiple files:

```bash
for f in *.epub; do
  pandoc "$f" -t epub3 -o "epub3_${f}"
  epubcheck "epub3_${f}"
done
```

#### Detecting EPUB Version

Before converting, check whether a file is already EPUB 3:

```bash
# Quick version check via the OPF package document
unzip -p book.epub OEBPS/content.opf 2>/dev/null | grep -o 'version="[^"]*"'

# Or use the metadata script for a full report
python3 scripts/epub_metadata.py book.epub
```

If the version is `"3.0"` or higher, conversion is not needed — just validate with epubcheck.

## Helper Scripts

Two Python helper scripts are bundled in the `scripts/` directory of this skill. Run them
directly — they have no dependencies beyond the Python 3 standard library.

- **`scripts/epub_metadata.py <file.epub> [--json]`** — Extract and display all metadata, TOC
  structure, spine order, and manifest contents from an EPUB file. Reports the EPUB version.
  With `--json`, outputs JSON-LD using Dublin Core vocabulary.
- **`scripts/epub_update.py <file.epub> [options]`** — Modify EPUB metadata, replace the cover
  image, or inject/remove files. Accepts `--metadata-file` for JSON-LD input (as produced by
  `epub_metadata.py --json`). Run with `--help` for full usage.

When running these scripts, use the full path relative to this skill's installation directory.
Locate the skill directory first:

```bash
# Find the skill directory
SKILL_DIR=$(find ~/.claude -path "*/skill-epub/scripts" -type d 2>/dev/null | head -1)
# Run a script
python3 "$SKILL_DIR/epub_metadata.py" book.epub
```

## EPUB Structure Reference

Read `references/epub-structure.md` for detailed information about the EPUB 3 format internals,
including the container layout, OPF package document, navigation documents, and content
document requirements.

## Workflow Guidelines

- Always use `-t epub3` when creating or converting EPUBs with pandoc.
- Always validate after creating or modifying an EPUB with `epubcheck`.
- When encountering an EPUB 2 file, convert it to EPUB 3 using pandoc before further processing.
- When the user provides content to convert, prefer Markdown as the intermediate format —
  pandoc handles it cleanly.
- For multi-chapter books, recommend one Markdown file per chapter with a metadata YAML file.
- When modifying an existing EPUB, always work on a copy — never modify the original in place.
- If an EPUB fails validation, use `epubcheck book.epub --json results.json` for structured
  error output, then fix issues systematically starting with errors before warnings.
