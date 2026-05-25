# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Claude Skill for EPUB file operations. `SKILL.md` is the skill definition (YAML frontmatter +
workflow guides) that gets loaded into Claude's context. The two Python scripts in `scripts/` are
helper tools invoked by the skill at runtime.

## Running the Scripts

```bash
uv run --no-project scripts/epub_metadata.py book.epub        # human-readable output
uv run --no-project scripts/epub_metadata.py book.epub --json # JSON-LD with Dublin Core vocabulary
python3 scripts/epub_update.py book.epub --title "X" # modify metadata
python3 scripts/epub_update.py book.epub --metadata-file meta.json  # apply JSON-LD metadata
```

`epub_metadata.py` declares its `rdflib` dependency inline (PEP 723), so `uv run` provisions
it automatically — no separate install. `epub_update.py` uses only the standard library.

External tools: `pandoc`, `epubcheck`, and `uv` (install via `brew install pandoc epubcheck uv`).

## Architecture

- **`SKILL.md`** — Skill definition loaded by Claude. Frontmatter defines trigger phrases and
  allowed tools. Body contains workflow guides for EPUB operations.
- **`scripts/epub_metadata.py`** — Extracts metadata, TOC, spine, manifest from an EPUB.
  Outputs JSON-LD (Dublin Core `dc:`/`dcterms:` vocabulary) or human-readable text.
- **`scripts/epub_update.py`** — Modifies EPUB metadata, cover images, and internal files.
  Accepts JSON-LD input from `epub_metadata.py --json` for round-tripping.
- **`references/epub-structure.md`** — EPUB 3 format specification reference.

## Key Implementation Patterns

**JSON-LD metadata round-trip**: `epub_metadata.py --json` uses rdflib to build a proper RDF
graph from OPF metadata and serializes it as JSON-LD. DC elements with `<meta refines="#id">`
become structured nodes (BNodes with `rdf:value` + refinement predicates like `epub:role`).
Non-refining `<meta property>` elements (e.g., `schema:accessMode`, `dcterms:modified`) become
direct predicates on the publication node. `epub_update.py --metadata-file` reads that format
back, using `_extract_rdf_value()` to handle both simple literals and structured BNode objects
when mapping `dc:*` keys to CLI field names.

**XML namespace handling**: Both scripts define an `NS` dict mapping prefixes to URIs.
`epub_update.py` registers namespaces via `ET.register_namespace()` so prefixes survive
serialization. The OPF default namespace is registered as `""` (not `"opf"`).

**EPUB ZIP repackaging**: EPUBs require `mimetype` as the first entry, stored uncompressed
(`ZIP_STORED`). `epub_update.py` writes to a temp file first, then moves to the output path
on success.

**EPUB 2 vs 3 navigation**: `epub_metadata.py` tries EPUB 3 nav document first (manifest item
with `properties="nav"`), falls back to NCX (`application/x-dtbncx+xml` media type).
