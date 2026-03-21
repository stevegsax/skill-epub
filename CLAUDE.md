# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Claude Skill for EPUB file operations. `SKILL.md` is the skill definition (YAML frontmatter +
workflow guides) that gets loaded into Claude's context. The two Python scripts in `scripts/` are
helper tools invoked by the skill at runtime.

## Running the Scripts

```bash
python3 scripts/epub_metadata.py book.epub          # human-readable output
python3 scripts/epub_metadata.py book.epub --json    # JSON-LD with Dublin Core vocabulary
python3 scripts/epub_update.py book.epub --title "X" # modify metadata
python3 scripts/epub_update.py book.epub --metadata-file meta.json  # apply JSON-LD metadata
```

Both scripts use only the Python 3 standard library — no pip dependencies.

External tools: `pandoc` and `epubcheck` (install via `brew install pandoc epubcheck`).

## Architecture

- **`SKILL.md`** — Skill definition loaded by Claude. Frontmatter defines trigger phrases and
  allowed tools. Body contains workflow guides for EPUB operations.
- **`scripts/epub_metadata.py`** — Extracts metadata, TOC, spine, manifest from an EPUB.
  Outputs JSON-LD (Dublin Core `dc:`/`dcterms:` vocabulary) or human-readable text.
- **`scripts/epub_update.py`** — Modifies EPUB metadata, cover images, and internal files.
  Accepts JSON-LD input from `epub_metadata.py --json` for round-tripping.
- **`references/epub-structure.md`** — EPUB 3 format specification reference.

## Key Implementation Patterns

**JSON-LD metadata round-trip**: `epub_metadata.py --json` produces JSON-LD with `@context`,
`@type`, and `dc:`-prefixed keys. `epub_update.py --metadata-file` reads that format back,
mapping `dc:*` keys to CLI field names. `dcterms:modified` is handled separately as an OPF
`<meta property="...">` element rather than a DC element. Non-DC meta entries go into an
`epubMeta` array to avoid data loss.

**XML namespace handling**: Both scripts define an `NS` dict mapping prefixes to URIs.
`epub_update.py` registers namespaces via `ET.register_namespace()` so prefixes survive
serialization. The OPF default namespace is registered as `""` (not `"opf"`).

**EPUB ZIP repackaging**: EPUBs require `mimetype` as the first entry, stored uncompressed
(`ZIP_STORED`). `epub_update.py` writes to a temp file first, then moves to the output path
on success.

**EPUB 2 vs 3 navigation**: `epub_metadata.py` tries EPUB 3 nav document first (manifest item
with `properties="nav"`), falls back to NCX (`application/x-dtbncx+xml` media type).
