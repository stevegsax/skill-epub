# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Claude Skill for EPUB file operations. `SKILL.md` is the skill definition (YAML frontmatter
plus the workflow guide) that gets loaded into Claude's context. The Python scripts in
`scripts/` are helper tools the skill runs at runtime.

## Running the Scripts and Tests

```bash
uv run --no-project scripts/epub_metadata.py book.epub --summary
uv run --no-project scripts/epub_update.py book.epub -o new.epub --title "X"
uv run --no-project scripts/epub_upgrade.py old.epub -o new.epub

uv run --no-project --with pytest pytest tests          # tests (epubcheck optional)
uvx ruff check scripts tests && uvx ruff format scripts tests
```

Each script declares its dependencies inline (PEP 723: lxml for the editing scripts, rdflib
for `epub_metadata.py`) and requires Python 3.10 or newer, so always run them through
`uv run --no-project`, never plain `python3`. External tools: `pandoc`, `epubcheck`, `uv`.

## Architecture

- `SKILL.md`: skill definition. Frontmatter follows the Agent Skills specification (`name`,
  `description`, `compatibility`, `allowed-tools`); scripts are referenced through
  `${CLAUDE_SKILL_DIR}`. Keep the body under 500 lines.
- `scripts/epub_common.py`: helpers with no third-party dependencies: namespaces, container
  parsing, href resolution, ISO date checks, and `write_epub()` (mimetype first and stored,
  temp file renamed into place, never overwrites the input).
- `scripts/epub_opf.py`: lxml editing of the package document and navigation files. All
  metadata, manifest, spine, cover, reference-rewriting, and nav-generation logic lives here
  and is shared by the two editing scripts.
- `scripts/epub_metadata.py`: read-only. Builds an RDF graph with rdflib and serializes it
  as JSON-LD; `--summary`, `--json`, `--path`.
- `scripts/epub_update.py`: metadata, cover, add and remove files. `--output` required.
- `scripts/epub_upgrade.py`: EPUB 2 to EPUB 3 while keeping the content files.
- `references/epub-structure.md`: EPUB 3.3 reference the skill points Claude to.
- `tests/`: pytest suite; `fixtures.py` builds valid EPUB 2 and EPUB 3 books from strings.

## Key Implementation Patterns

**JSON-LD metadata round-trip.** A refined element is an object with `rdf:value` and one key
per refinement; a refinement with a scheme is an object with `rdf:value` and `opf:scheme`.
EPUB 2 attributes are `opf:role`, `opf:file-as`, `opf:scheme`, `opf:event`. The `epub:`
prefix is the unprefixed package meta vocabulary (`http://idpf.org/epub/vocab/package/meta/#`);
`schema:` is `http://schema.org/` and `marc:` is `http://id.loc.gov/vocabulary/`, matching
EPUBCheck. `epub_opf.apply_metadata()` applies every key present, leaves absent keys alone,
and treats an empty list as removal. A plain string value changes text only and keeps the
element's refinements; a dict replaces the node.

**lxml, not ElementTree, for writing.** ElementTree drops the `opf:` prefix from attributes
and discards comments, which breaks EPUB 2 packages. `epub_opf.dump_opf()` keeps the source
prefixes and declares `dc:`/`opf:` at the root through `cleanup_namespaces`.

**Nothing is edited in place.** `check_output_path()` refuses an output equal to the input;
`write_epub()` writes a temp file and renames it. `dcterms:modified` is set to the current
UTC time on every EPUB 3 write unless `--modified` is given; dates are validated as ISO 8601.

**Manifest stays consistent.** `--add` creates the manifest item (and spine entry for XHTML);
`--remove` drops the item, spine entry, nav and NCX links, and guide references, and warns
about remaining references; a cover rename rewrites `src`/`href`/`url()` references.

**Validation in tests.** `assert_valid()` runs EPUBCheck when available and expects
`0 errors / 0 warnings`; new behaviour that writes a file should be covered by such a test.
