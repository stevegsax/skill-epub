---
name: managing-epubs
description: >-
  Creates, validates, inspects, updates, upgrades, and converts EPUB files (EPUB 3.3) with
  pandoc, EPUBCheck, and bundled Python scripts. Use when the user mentions EPUB, e-book,
  ebook, or .epub files, or asks to package Markdown or HTML as an e-book, read or change
  e-book metadata or covers, fix EPUBCheck errors, or upgrade an EPUB 2 file to EPUB 3.
  Not for PDF-only work or Kindle-only formats (MOBI, AZW3).
compatibility: >-
  Needs pandoc, EPUBCheck (Java), and uv installed on the local machine; the bundled scripts
  declare lxml and rdflib as PEP 723 dependencies, which uv fetches on first run. Written for
  Claude Code; the Claude API code-execution sandbox has none of these tools.
allowed-tools: Read Write Edit Glob Grep Bash(uv run --no-project ${CLAUDE_SKILL_DIR}/scripts/*) Bash(pandoc *) Bash(epubcheck *) Bash(unzip *) Bash(zipinfo *) Bash(jq *)
---

# Managing EPUBs

An EPUB is a ZIP archive of XHTML content, a package document (OPF) that lists every file
and holds the metadata, and a navigation document. Everything this skill produces targets
EPUB 3 (W3C EPUB 3.3). The bundled scripts in `scripts/` do the structural work; pandoc
creates and converts books; EPUBCheck validates them. Every script writes a new file and
refuses to overwrite its input, and every script exits non-zero with a message on stderr
when it cannot do what was asked, so check exit codes rather than assuming success.

## Tools

Confirm the tools before starting, because versions differ and `epubcheck` is often missing:

```bash
pandoc --version | head -1      # 3.x expected; EPUB 3 is the default EPUB output
epubcheck --version             # 5.x expected
uv --version                    # runs the scripts and fetches their dependencies
```

If something is missing, tell the user and give the install command: on macOS
`brew install pandoc epubcheck uv`; on Debian or Ubuntu `sudo apt install pandoc default-jre`
plus EPUBCheck from <https://github.com/w3c/epubcheck/releases> (unzip it and put a wrapper
named `epubcheck` on PATH) and uv from <https://docs.astral.sh/uv/>.

## Scripts

Run each script with `uv run --no-project` so uv provides lxml and rdflib without touching
any project environment. `${CLAUDE_SKILL_DIR}` is the folder that holds this file; where it
is not substituted, use the path of this skill's `scripts/` folder instead.

| Script | Purpose |
| --- | --- |
| `epub_metadata.py BOOK [--json \| --summary \| --path NAME]` | Read metadata (as JSON-LD), version, archive paths, TOC, spine, manifest |
| `epub_update.py BOOK -o OUT [options]` | Change metadata, the cover, or files; keeps manifest, spine, and navigation consistent |
| `epub_upgrade.py BOOK -o OUT` | Convert EPUB 2 to EPUB 3 without rebuilding the content |

All three accept `--json` for a machine-readable report. Pass `--help` for the full options.

```bash
S="${CLAUDE_SKILL_DIR}/scripts"
uv run --no-project "$S/epub_metadata.py" book.epub --summary
```

## Inspect a book

Start with the summary. It is small enough to read whole and tells you the version, the
metadata, where the package, navigation, NCX, and cover files are inside the archive, and
whether any file the package lists is missing:

```bash
uv run --no-project "$S/epub_metadata.py" book.epub --summary
```

Use `--json` only when the manifest, spine, or TOC are needed, and filter it with `jq`.
Archive paths differ between producers (pandoc writes `EPUB/content.opf`, other tools
`OEBPS/content.opf`), so never guess a path; ask the script, then read the file:

```bash
opf=$(uv run --no-project "$S/epub_metadata.py" book.epub --path opf) && unzip -p book.epub "$opf"
nav=$(uv run --no-project "$S/epub_metadata.py" book.epub --path nav) && unzip -p book.epub "$nav"
```

`--path` exits 2 when the book has no such file (an EPUB 2 book has no nav document).
`zipinfo -1 book.epub` lists every archive entry. To get the text of a book, convert it with
pandoc: `pandoc book.epub -t markdown -o book.md` (also `-t plain`, `-t html`).

## Create a book with pandoc

Pandoc builds an EPUB 3 from Markdown, HTML, DOCX, LaTeX, or any other input it reads.
Keep the metadata in a YAML file and pass it with `--metadata-file`: it is the only way to
express lists (several authors or subjects) and identifiers with a scheme, and it avoids
shell-quoting mistakes in titles and descriptions.

```yaml
# metadata.yaml
title: Book Title
author:
  - First Author
  - Second Author
date: 2026-01-01          # ISO 8601; pandoc writes it to dc:date as given
lang: en-US
publisher: Publisher Name
rights: All rights reserved
subject: [Fiction, Adventure]
identifier:
  - scheme: ISBN
    text: 978-0-000-00000-0
description: A brief description of the book.
```

```bash
pandoc input.md -t epub3 -o book.epub --metadata-file=metadata.yaml

# Several chapters, a cover, a stylesheet, and a nav document two levels deep
pandoc ch01.md ch02.md ch03.md -t epub3 -o book.epub \
  --metadata-file=metadata.yaml --epub-cover-image=cover.jpg --css=style.css \
  --toc --toc-depth=2 --split-level=1 --top-level-division=chapter
```

Pandoc 3.9 marks `--epub-chapter-level` as deprecated in favour of `--split-level`, which
every pandoc 3 release accepts. Pandoc adds the `dcterms:modified` timestamp itself. For a
book the user is writing, one Markdown file per chapter plus a metadata file is the
arrangement that converts most cleanly.

## Validate

Validate every EPUB after writing it. EPUBCheck exits 0 when there are no errors and 1 when
there are; `--failonwarnings` makes warnings fail too. Read the message IDs: `RSC-005` is a
schema violation in the file and line named, `RSC-007` a reference to a file that is not in
the archive, `RSC-012` a link to a fragment that does not exist, `HTM-004` a non-HTML5
DOCTYPE, `OPF-053` a date that is not ISO 8601.

```bash
epubcheck book.epub                         # readable report
epubcheck book.epub --json report.json      # structured report for larger books
jq '.messages[] | select(.severity=="ERROR") | {ID, message, locations: [.locations[].path]}' report.json
```

Fix errors before warnings and validate again after each round. EPUBCheck checks
conformance only: a book can pass and still have lost its styling or cover, so compare
`epub_metadata.py --summary` output before and after a conversion as well.

## Change metadata, cover, or files

`epub_update.py` edits the package document in place of its structure, so the prefixes,
comments, ids, and refinements already in the file survive. It requires `--output` and
writes `dcterms:modified` as the current UTC time on every write (give `--modified` to set a
specific timestamp). Dates must be ISO 8601 (`2026`, `2026-01`, or `2026-01-31`); anything
else is rejected rather than written.

```bash
uv run --no-project "$S/epub_update.py" book.epub -o new.epub --title "New Title" --author "A. Writer"
uv run --no-project "$S/epub_update.py" book.epub -o new.epub \
  --subject History --subject Travel \
  --set schema:accessibilitySummary="Text with structural navigation" \
  --remove-meta dc:rights
uv run --no-project "$S/epub_update.py" book.epub -o new.epub --cover cover.png
uv run --no-project "$S/epub_update.py" book.epub -o new.epub --add notes.xhtml:EPUB/text/notes.xhtml
uv run --no-project "$S/epub_update.py" book.epub -o new.epub --remove EPUB/text/old.xhtml
```

A plain `--author NAME` changes the name and keeps the author's role and sort form. Repeated
flags set the whole list for that field, in order. `--set KEY=VALUE` takes any key the
metadata JSON uses (`dc:source`, `dcterms:issued`, `schema:accessMode`, `a11y:certifiedBy`).
`--cover` replaces the existing cover image, renaming it when the format changes and
updating every reference to it; without an existing cover it adds one. `--add` lists the
file in the manifest and puts an XHTML document at the end of the spine. `--remove` deletes
the file and its manifest item, spine entry, nav and NCX links, and guide entries, and warns
about any other document that still links to it.

For larger edits, round-trip the metadata through a file. Every key present in the file is
applied, keys absent from the file are left alone, and an empty list removes a key, so
nothing disappears unless the file says so:

```bash
uv run --no-project "$S/epub_metadata.py" book.epub --summary > meta.json
# edit meta.json: change "metadata"."dc:title", add "dc:contributor", set "dc:subject": [] ...
uv run --no-project "$S/epub_update.py" book.epub -o new.epub --metadata-file meta.json
```

In that JSON, a value with refinements is an object with `rdf:value` plus one key per
refinement (`epub:role`, `epub:file-as`, `epub:identifier-type`); a refinement that has a
scheme is itself an object with `rdf:value` and `opf:scheme`. EPUB 2 attributes appear as
`opf:role`, `opf:file-as`, `opf:scheme`, and `opf:event`, and go back as attributes on an
EPUB 2 package or as refinements on an EPUB 3 package. Flags override values from the file.

## Upgrade EPUB 2 to EPUB 3

Check the version first (`--summary` reports it). For a `2.0` package, use the upgrade
script. It keeps every content file, stylesheet, image, and file name, and changes only what
EPUB 3 requires: the package version, the EPUB 2 `opf:` attributes (converted to
refinements), empty metadata elements (removed), `dcterms:modified` (added), the cover item
(marked `cover-image`), a navigation document (generated from the NCX, with landmarks from
the guide), and in each XHTML file the DOCTYPE and any HTML named entities that XML does not
define.

```bash
uv run --no-project "$S/epub_upgrade.py" old.epub -o new.epub && epubcheck new.epub
```

The report lists each change and anything it had to drop. EPUBCheck then reports whatever
the content itself has that EPUB 3 forbids (presentational attributes such as `align`,
`<center>`, or `<font>`); fix those in the XHTML with `--remove`/`--add` or by extracting,
editing, and repacking (see the reference file for the packing rules).

Converting with pandoc (`pandoc old.epub -t epub3 -o new.epub`) is a different operation: it
rebuilds the book from pandoc's document model, which drops the publisher's CSS classes and
cover markup, renames every file, and may insert a title page. Use it only when the user
wants a reflowed book rather than their book upgraded. Pandoc also ignores `--metadata-file`
when the input is an EPUB, so change metadata afterwards with `epub_update.py`. Pandoc keeps
a `dc:date` that is `YYYY` or `YYYY-MM-DD` and blanks any other form (a month name, a full
timestamp), which EPUBCheck rejects; set a valid ISO date with `--date` afterwards.

## Convert to and from other formats

```bash
pandoc book.epub -o book.docx
pandoc book.epub -o book.pdf          # needs a PDF engine such as LaTeX or wkhtmltopdf
pandoc document.docx -t epub3 -o book.epub --metadata-file=metadata.yaml
```

## Accessibility metadata

EPUB 3 books sold in the European Union have needed accessibility metadata since June 2025
(European Accessibility Act), and retailers increasingly require it. Pandoc writes basic
`schema:accessMode`, `schema:accessibilityFeature`, and `schema:accessibilityHazard`
values; add a human-readable `schema:accessibilitySummary` and, when the book has been
checked, `dcterms:conformsTo` with the EPUB Accessibility conformance URL. Set them with
`--set` or in the metadata file. EPUBCheck validates the syntax of these properties but not
the book's accessibility; the DAISY Ace checker (`npm install -g @daisy/ace`) does that.

## Working rules

- Scripts never modify a file in place, so the user's original is always kept; pass `-o`.
- Validate with `epubcheck` after every write, and re-validate after each round of fixes.
- Dates written to a book are ISO 8601.
- Give pandoc metadata through `--metadata-file`, not `--metadata` flags.
- For an EPUB 2 input, upgrade with `epub_upgrade.py`; reserve pandoc for reflowing.
- When a command can fail quietly (an `unzip -p` of a guessed path, a `grep` that prints
  nothing), check its exit status or use the script that reports paths and errors.

## Reference

`references/epub-structure.md` describes the EPUB 3 container, package document, navigation
document, content document requirements, media types, the EPUB 2 to EPUB 3 differences, and
the ZIP packing rules for building an EPUB by hand.
