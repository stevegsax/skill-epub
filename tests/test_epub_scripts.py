"""End-to-end tests for the EPUB skill scripts.

Run from the repository root:

    uv run --no-project --with pytest pytest tests

Each script is run the way the skill runs it (`uv run --no-project`), so
the tests also check the PEP 723 dependency headers. When `epubcheck` is on
PATH, every EPUB the scripts write is validated with it; otherwise those
assertions are skipped and the test says so.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import fixtures  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
EPUBCHECK = shutil.which("epubcheck")
TINY_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b"
)


def run_script(name: str, *args: str, expect: int = 0) -> subprocess.CompletedProcess:
    cmd = ["uv", "run", "--no-project", str(SCRIPTS / name), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == expect, (
        f"{' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc


def metadata(epub: Path, *extra: str) -> dict:
    return json.loads(run_script("epub_metadata.py", str(epub), "--json", *extra).stdout)


def update(epub: Path, out: Path, *args: str, expect: int = 0) -> dict:
    proc = run_script("epub_update.py", str(epub), "-o", str(out), "--json", *args, expect=expect)
    return json.loads(proc.stdout)


def read(epub: Path, name: str) -> str:
    with zipfile.ZipFile(epub) as zf:
        return zf.read(name).decode("utf-8")


def names(epub: Path) -> list[str]:
    with zipfile.ZipFile(epub) as zf:
        return zf.namelist()


def assert_valid(epub: Path) -> None:
    """Assert EPUBCheck reports no errors or warnings (skipped if absent)."""
    if not EPUBCHECK:
        pytest.skip("epubcheck not on PATH; validation skipped")
    proc = subprocess.run([EPUBCHECK, str(epub)], capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    assert "0 errors / 0 warnings" in out, out


@pytest.fixture
def epub2(tmp_path: Path) -> Path:
    return fixtures.make_epub2(tmp_path / "old.epub")


@pytest.fixture
def epub3(tmp_path: Path) -> Path:
    return fixtures.make_epub3(tmp_path / "new.epub")


# --- fixtures themselves ----------------------------------------------------


def test_fixtures_are_valid(epub2, epub3):
    assert_valid(epub2)
    assert_valid(epub3)


# --- epub_metadata.py --------------------------------------------------------


def test_metadata_epub3(epub3):
    info = metadata(epub3)
    assert info["version"] == "3.0"
    assert info["toc_type"] == "epub3-nav"
    m = info["metadata"]
    assert m["@id"] == "urn:isbn:9780000000001"
    assert [c["rdf:value"] for c in m["dc:creator"]] == ["Jane Doe", "John Roe"]
    assert m["dc:creator"][0]["epub:role"] == {"rdf:value": "aut", "opf:scheme": "marc:relators"}
    assert m["dc:creator"][0]["epub:file-as"] == "Doe, Jane"
    assert m["dc:subject"] == ["Fiction", "Adventure"]
    assert m["dc:title"] == {"rdf:value": "New Book", "epub:title-type": "main"}
    assert m["schema:accessibilityFeature"] == ["structuralNavigation", "tableOfContents"]
    ctx = m["@context"]
    assert ctx["schema"] == "http://schema.org/"
    assert ctx["marc"] == "http://id.loc.gov/vocabulary/"
    assert ctx["epub"] == "http://idpf.org/epub/vocab/package/meta/#"
    assert info["paths"] == {
        "opf": "OEBPS/content.opf",
        "opf_dir": "OEBPS",
        "nav": "OEBPS/nav.xhtml",
        "ncx": "OEBPS/toc.ncx",
        "cover_image": "OEBPS/images/cover.jpg",
        "spine": ["OEBPS/ch1.xhtml", "OEBPS/text/ch2.xhtml"],
    }
    assert info["toc"][0]["children"][0]["src"] == "ch1.xhtml#lead"


def test_metadata_epub2(epub2):
    info = metadata(epub2)
    assert info["version"] == "2.0"
    assert info["toc_type"] == "ncx"
    m = info["metadata"]
    assert m["dc:creator"][0] == {
        "rdf:value": "John Doe",
        "opf:role": "aut",
        "opf:file-as": "Doe, John",
    }
    assert (
        m["@id"] == "urn:uuid:11111111-2222-3333-4444-555555555555"
    )  # unique-identifier, not first
    assert m["dc:subject"] == "History"  # the empty one is skipped
    assert info["legacy_meta"] == [{"name": "cover", "content": "cover-img"}]
    assert info["paths"]["cover_image"] == "OEBPS/images/cover.jpg"
    assert info["paths"]["nav"] is None


def test_metadata_summary_and_path(epub3):
    summary = json.loads(run_script("epub_metadata.py", str(epub3), "--summary").stdout)
    assert set(summary) == {
        "file",
        "version",
        "metadata",
        "legacy_meta",
        "toc_type",
        "paths",
        "file_count",
        "total_size",
    }
    assert (
        run_script("epub_metadata.py", str(epub3), "--path", "opf").stdout.strip()
        == "OEBPS/content.opf"
    )
    proc = run_script("epub_metadata.py", str(epub3), "--path", "nav").stdout.strip()
    assert proc == "OEBPS/nav.xhtml"
    fixtures.make_epub2(epub3.with_name("two.epub"))
    run_script("epub_metadata.py", str(epub3.with_name("two.epub")), "--path", "nav", expect=2)


def test_metadata_errors(tmp_path):
    run_script("epub_metadata.py", str(tmp_path / "missing.epub"), expect=1)
    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"not a zip")
    proc = run_script("epub_metadata.py", str(bad), expect=1)
    assert "Error" in proc.stderr


# --- epub_update.py ----------------------------------------------------------


def test_update_requires_output_and_refuses_in_place(epub3, tmp_path):
    proc = subprocess.run(
        ["uv", "run", "--no-project", str(SCRIPTS / "epub_update.py"), str(epub3), "--title", "X"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2 and "--output is required" in proc.stderr
    report = update(epub3, epub3, "--title", "X", expect=1)
    assert "never modify" in report["error"]
    assert read(epub3, "OEBPS/content.opf").count("New Book") == 1


def test_update_epub2_keeps_prefixes_and_comments(epub2, tmp_path):
    out = tmp_path / "out.epub"
    report = update(epub2, out, "--title", "Renamed")
    assert "Set dc:title: Renamed" in report["changes"]
    opf = read(out, "OEBPS/content.opf")
    assert "<!-- publisher comment -->" in opf
    assert 'opf:role="aut"' in opf and 'opf:file-as="Doe, John"' in opf
    assert "dcterms:modified" not in opf  # EPUB 2 has no such element
    assert_valid(out)


def test_update_string_keeps_refinements(epub3, tmp_path):
    out = tmp_path / "out.epub"
    update(epub3, out, "--author", "Jane Smith")
    m = metadata(out)["metadata"]
    assert m["dc:creator"] == {
        "rdf:value": "Jane Smith",
        "epub:role": {"rdf:value": "aut", "opf:scheme": "marc:relators"},
        "epub:file-as": "Doe, Jane",
    }
    assert m["dcterms:modified"] != "2026-01-01T00:00:00Z"
    assert_valid(out)


def test_update_set_remove_and_dates(epub3, tmp_path):
    out = tmp_path / "out.epub"
    report = update(
        epub3,
        out,
        "--subject",
        "Only",
        "--set",
        "schema:accessibilitySummary=Plain",
        "--set",
        "dc:source=urn:isbn:9780000000002",
        "--remove-meta",
        "schema:accessibilityHazard",
        "--date",
        "2024-06",
        "--modified",
        "2025-02-03T04:05:06Z",
    )
    assert "Removed dc:subject: Adventure" in report["changes"]
    m = metadata(out)["metadata"]
    assert m["dc:subject"] == "Only"
    assert m["schema:accessibilitySummary"] == "Plain"
    assert m["dc:source"] == "urn:isbn:9780000000002"
    assert "schema:accessibilityHazard" not in m
    assert m["dc:date"] == "2024-06"
    assert m["dcterms:modified"] == "2025-02-03T04:05:06Z"
    assert_valid(out)
    report = update(epub3, tmp_path / "bad.epub", "--date", "June 2024", expect=1)
    assert "ISO 8601" in report["error"]
    report = update(epub3, tmp_path / "bad.epub", "--modified", "2024-06-01", expect=1)
    assert "--modified must look like" in report["error"]


def test_update_metadata_file_round_trip(epub3, tmp_path):
    meta_file = tmp_path / "meta.json"
    meta_file.write_text(run_script("epub_metadata.py", str(epub3), "--summary").stdout)
    same = tmp_path / "same.epub"
    update(epub3, same, "--metadata-file", str(meta_file))
    before = metadata(epub3)["metadata"]
    after = metadata(same)["metadata"]
    before.pop("dcterms:modified"), after.pop("dcterms:modified")
    assert before == after
    assert_valid(same)

    data = json.loads(meta_file.read_text())
    data["metadata"]["dc:title"] = {"rdf:value": "Edited", "epub:title-type": "main"}
    data["metadata"]["dc:creator"][0]["epub:role"]["rdf:value"] = "edt"
    data["metadata"]["dc:subject"] = []  # explicit removal
    data["metadata"]["dc:contributor"] = [{"rdf:value": "Ed Itor", "epub:role": "edt"}]
    meta_file.write_text(json.dumps(data))
    out = tmp_path / "edited.epub"
    report = update(epub3, out, "--metadata-file", str(meta_file))
    assert "Removed dc:subject: Fiction" in report["changes"]
    m = metadata(out)["metadata"]
    assert m["dc:title"]["rdf:value"] == "Edited"
    assert m["dc:creator"][0]["epub:role"]["rdf:value"] == "edt"
    assert m["dc:creator"][1]["epub:role"]["rdf:value"] == "aut"
    assert "dc:subject" not in m
    assert m["dc:contributor"] == {"rdf:value": "Ed Itor", "epub:role": "edt"}
    assert m["schema:accessMode"] == "textual"  # untouched keys survive
    assert_valid(out)


def test_update_metadata_file_epub2(epub2, tmp_path):
    meta_file = tmp_path / "meta.json"
    data = json.loads(run_script("epub_metadata.py", str(epub2), "--summary").stdout)
    data["metadata"]["dc:creator"][0]["rdf:value"] = "Jane Q"
    meta_file.write_text(json.dumps(data))
    out = tmp_path / "out.epub"
    update(epub2, out, "--metadata-file", str(meta_file))
    opf = read(out, "OEBPS/content.opf")
    assert '<dc:identifier id="BookId" opf:scheme="UUID">urn:uuid' in opf
    assert 'opf:file-as="Doe, John"' in opf and "Jane Q" in opf
    assert_valid(out)
    report = update(epub2, tmp_path / "bad.epub", "--set", "schema:accessMode=textual", expect=1)
    assert "EPUB 2" in report["error"]


def test_update_remove_document(epub3, tmp_path):
    out = tmp_path / "out.epub"
    report = update(epub3, out, "--remove", "OEBPS/text/ch2.xhtml")
    assert report["warnings"] == []
    assert "OEBPS/text/ch2.xhtml" not in names(out)
    opf = read(out, "OEBPS/content.opf")
    assert 'id="ch2"' not in opf and 'idref="ch2"' not in opf
    assert "ch2.xhtml" not in read(out, "OEBPS/nav.xhtml")
    assert "ch2.xhtml" not in read(out, "OEBPS/toc.ncx")
    assert_valid(out)
    # Removing a document others link to is reported, not hidden.
    report = update(epub3, tmp_path / "out2.epub", "--remove", "OEBPS/ch1.xhtml")
    assert any("text/ch2.xhtml still references" in w for w in report["warnings"])
    report = update(epub3, tmp_path / "bad.epub", "--remove", "OEBPS/nav.xhtml", expect=1)
    assert "navigation document" in report["error"]


def test_update_add_files(epub3, tmp_path):
    extra = tmp_path / "extra.xhtml"
    extra.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Extra</title></head>'
        "<body><p>Extra</p></body></html>"
    )
    css = tmp_path / "extra.css"
    css.write_text("p { margin: 0 }")
    out = tmp_path / "out.epub"
    report = update(
        epub3, out, "--add", f"{extra}:OEBPS/text/extra.xhtml", "--add", f"{css}:OEBPS/extra.css"
    )
    opf = read(out, "OEBPS/content.opf")
    assert 'href="text/extra.xhtml" media-type="application/xhtml+xml"' in opf
    assert 'href="extra.css" media-type="text/css"' in opf
    assert opf.count("<itemref") == 3
    assert any("end of the spine" in c for c in report["changes"])
    assert_valid(out)
    report = update(epub3, tmp_path / "bad.epub", "--add", f"{css}:OEBPS/style.css", expect=1)
    assert "already exists" in report["error"]


def test_update_cover_replace_with_rename(epub3, epub2, tmp_path):
    gif = tmp_path / "new.gif"
    gif.write_bytes(TINY_GIF)
    out = tmp_path / "out.epub"
    report = update(epub3, out, "--cover", str(gif))
    assert "OEBPS/images/cover.gif" in names(out) and "OEBPS/images/cover.jpg" not in names(out)
    assert 'href="images/cover.gif" media-type="image/gif" properties="cover-image"' in read(
        out, "OEBPS/content.opf"
    )
    assert 'src="images/cover.gif"' in read(out, "OEBPS/ch1.xhtml")
    assert "url(images/cover.gif)" in read(out, "OEBPS/style.css")
    assert any("Renamed cover" in c for c in report["changes"])
    assert_valid(out)
    out2 = tmp_path / "out2.epub"
    update(epub2, out2, "--cover", str(gif))
    assert '<meta name="cover" content="cover-img"/>' in read(out2, "OEBPS/content.opf")
    assert_valid(out2)


def test_update_cover_add(epub3, tmp_path):
    png = tmp_path / "c.png"
    png.write_bytes(fixtures.tiny_png())
    stripped = fixtures.make_epub3(tmp_path / "nocover.epub", cover=False)
    assert_valid(stripped)
    assert metadata(stripped)["paths"]["cover_image"] is None
    out = tmp_path / "out.epub"
    update(stripped, out, "--cover", str(png))
    assert 'properties="cover-image"' in read(out, "OEBPS/content.opf")
    assert "OEBPS/images/cover.png" in names(out)
    assert_valid(out)


def test_update_dry_run_writes_nothing(epub3, tmp_path):
    out = tmp_path / "never.epub"
    proc = run_script("epub_update.py", str(epub3), "--dry-run", "--title", "T", "--json")
    assert json.loads(proc.stdout)["dry_run"] is True
    assert not out.exists()


def test_update_nothing_to_do(epub3, tmp_path):
    report = update(epub3, tmp_path / "out.epub", expect=1)
    assert "no changes requested" in report["error"]


# --- epub_upgrade.py ---------------------------------------------------------


def test_upgrade_epub2(epub2, tmp_path):
    out = tmp_path / "up.epub"
    proc = run_script("epub_upgrade.py", str(epub2), "-o", str(out), "--json")
    report = json.loads(proc.stdout)
    assert report["upgraded"] is True and report["warnings"] == []
    assert_valid(out)

    opf = read(out, "OEBPS/content.opf")
    assert 'version="3.0"' in opf
    assert "opf:role" not in opf and "opf:scheme" not in opf and "opf:event" not in opf
    assert "<!-- publisher comment -->" in opf
    assert 'property="dcterms:modified"' in opf
    assert 'property="dcterms:created">2000-12<' in opf
    assert "<dc:subject></dc:subject>" not in opf
    assert 'properties="cover-image"' in opf
    assert 'properties="nav"' in opf

    m = metadata(out)["metadata"]
    assert m["dc:creator"][0]["epub:role"] == {"rdf:value": "aut", "opf:scheme": "marc:relators"}
    assert m["dc:creator"][0]["epub:file-as"] == "Doe, John"
    assert m["dc:creator"][1]["epub:role"]["rdf:value"] == "ill"
    assert m["dc:identifier"][0]["epub:identifier-type"] == "ISBN"
    assert m["dc:date"] == "2001-05-01"

    nav = read(out, "OEBPS/nav.xhtml")
    assert (
        'epub:type="toc"' in nav
        and 'href="ch1.xhtml#lead"' in nav
        and 'href="text/ch2.xhtml"' in nav
    )
    assert 'epub:type="landmarks"' in nav and 'epub:type="bodymatter"' in nav
    ch1 = read(out, "OEBPS/ch1.xhtml")
    assert ch1.count("<!DOCTYPE html>") == 1 and "&nbsp;" not in ch1 and "&#160;" in ch1
    assert 'class="lead"' in ch1  # content untouched otherwise
    assert read(out, "OEBPS/style.css") == fixtures.CSS
    assert names(out)[0] == "mimetype"


def test_upgrade_leaves_epub3_alone(epub3, tmp_path):
    out = tmp_path / "up.epub"
    proc = run_script("epub_upgrade.py", str(epub3), "-o", str(out))
    assert "already EPUB 3.0" in proc.stdout
    assert not out.exists()


def test_upgraded_file_is_editable(epub2, tmp_path):
    up = tmp_path / "up.epub"
    run_script("epub_upgrade.py", str(epub2), "-o", str(up))
    out = tmp_path / "edited.epub"
    update(up, out, "--author", "Someone Else", "--set", "schema:accessMode=textual")
    assert_valid(out)
