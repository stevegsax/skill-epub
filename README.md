# skill-epub

A Claude Code skill for creating, validating, inspecting, updating, upgrading, and
converting EPUB files. Everything it produces targets EPUB 3 (W3C EPUB 3.3).

## Installation

The skill's name is `managing-epubs` (a gerund, as Anthropic's skill-authoring guidance
suggests, and specific enough that a passing mention of "epub" does not invoke it). The Agent
Skills specification expects a skill to live in a directory of that name, so clone or symlink
this repository there:

```bash
git clone https://github.com/stevegsax/skill-epub ~/.claude/skills/managing-epubs
# or, inside a project: git clone https://github.com/stevegsax/skill-epub .claude/skills/managing-epubs
```

Then install the command-line tools the skill drives:

```bash
brew install pandoc epubcheck uv            # macOS
```

On Linux, install pandoc and a Java runtime from the distribution, EPUBCheck from
<https://github.com/w3c/epubcheck/releases>, and uv from <https://docs.astral.sh/uv/>.

The Python scripts declare their dependencies (lxml, rdflib) in PEP 723 headers, so
`uv run --no-project` fetches them on first use; there is no separate install step.

## What it does

When loaded, the skill triggers on EPUB-related requests and guides Claude through:

- Creating EPUB 3 files from Markdown or HTML with pandoc, taking the metadata from a YAML
  file so lists (several authors, subjects) and identifiers with a scheme survive intact.
- Validating with EPUBCheck and reading its message IDs.
- Reading metadata as JSON-LD (Dublin Core and the EPUB package vocabularies), plus the
  archive paths of the package, navigation, NCX, and cover files.
- Changing metadata, the cover image, and the files in a book while keeping the manifest,
  spine, and navigation consistent, always writing a new file.
- Upgrading EPUB 2 files to EPUB 3 without rebuilding the content.
- Converting between EPUB and other formats with pandoc.
- Adding accessibility metadata.

## Repository layout

```text
skill-epub/
├── SKILL.md                     skill definition (frontmatter + workflow guide)
├── CLAUDE.md                    guidance for Claude Code when editing this repository
├── README.md                    this file
├── ruff.toml                    lint and format settings
├── scripts/
│   ├── epub_metadata.py         read metadata, paths, TOC, spine, manifest
│   ├── epub_update.py           change metadata, cover, files
│   ├── epub_upgrade.py          EPUB 2 → EPUB 3, content kept intact
│   ├── epub_opf.py              shared lxml editing of package and nav documents
│   └── epub_common.py           shared helpers (paths, dates, ZIP writing)
├── references/
│   └── epub-structure.md        EPUB 3 format reference
└── tests/
    ├── fixtures.py              builds valid EPUB 2 and EPUB 3 test books
    └── test_epub_scripts.py     end-to-end tests of the three scripts
```

## Using the scripts directly

```bash
uv run --no-project scripts/epub_metadata.py book.epub              # readable summary
uv run --no-project scripts/epub_metadata.py book.epub --summary    # short JSON
uv run --no-project scripts/epub_metadata.py book.epub --json       # everything
uv run --no-project scripts/epub_metadata.py book.epub --path opf   # archive path of the OPF

uv run --no-project scripts/epub_update.py book.epub -o new.epub --title "New Title"
uv run --no-project scripts/epub_update.py book.epub -o new.epub --metadata-file meta.json
uv run --no-project scripts/epub_update.py book.epub -o new.epub --cover cover.png

uv run --no-project scripts/epub_upgrade.py old.epub -o new.epub
```

Every script requires an output path and refuses to overwrite its input. `--json` on
`epub_update.py` and `epub_upgrade.py` prints a report of changes, warnings, and notes.

## Development

```bash
uv run --no-project --with pytest pytest tests    # end-to-end tests
uvx ruff check scripts tests && uvx ruff format --check scripts tests
```

The tests build their own EPUB 2 and EPUB 3 fixtures and run every script through
`uv run`. When `epubcheck` is on PATH, each file the scripts write is validated with it;
without it, those checks are skipped and reported as such.
