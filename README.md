# skill-epub

A Claude Code skill for creating, validating, querying, updating, and converting EPUB files.
All operations target EPUB 3 (W3C standard, EPUB 3.3).

## Installation

Copy or symlink this directory into your Claude Code skills location, then install the
command-line tools:

```bash
brew install pandoc epubcheck uv
```

`epub_metadata.py` declares its `rdflib` dependency inline (PEP 723), so `uv run`
provisions it automatically — there is no separate Python install step.

## What It Does

When loaded into Claude Code, the skill triggers on EPUB-related requests and provides
workflow guidance for:

- Creating EPUB 3 files from Markdown or HTML with `pandoc`, drawing metadata from a file
  rather than command-line flags so multi-valued fields (multiple authors, subjects) and
  identifiers with a scheme survive intact
- Validating EPUBs against EPUB 3.3 with `epubcheck`
- Extracting metadata, TOC, spine, and manifest as JSON-LD using Dublin Core vocabulary
- Modifying metadata, replacing cover images, and injecting or removing internal files
- Round-tripping metadata: extract as JSON-LD, edit, and apply the same file back
- Converting between EPUB and other formats (DOCX, HTML, PDF, …)
- Upgrading EPUB 2 files to EPUB 3, including the metadata and validation fix-ups that
  conversion requires

## Repository Structure

```text
skill-epub/
├── SKILL.md                      — Skill definition (loaded by Claude Code)
├── CLAUDE.md                     — Repo guidance for Claude Code
├── README.md                     — This file
├── scripts/
│   ├── epub_metadata.py          — Extract metadata/TOC/spine/manifest
│   └── epub_update.py            — Modify metadata, covers, and files
└── references/
    └── epub-structure.md         — EPUB 3 format specification reference
```

## Helper Scripts

The scripts can also be used standalone outside of Claude Code.

```bash
# Human-readable metadata summary
uv run --no-project scripts/epub_metadata.py book.epub

# JSON-LD output with Dublin Core vocabulary
uv run --no-project scripts/epub_metadata.py book.epub --json

# Modify metadata
python3 scripts/epub_update.py book.epub --title "New Title" --author "Author Name"

# Round-trip: extract, edit, apply
uv run --no-project scripts/epub_metadata.py book.epub --json > meta.json
# ... edit meta.json ...
python3 scripts/epub_update.py book.epub --metadata-file meta.json
```

`epub_metadata.py` declares its `rdflib` dependency inline (PEP 723), so `uv run`
provisions it automatically — no separate install. `epub_update.py` uses only the
Python 3 standard library, so it runs under plain `python3`.
