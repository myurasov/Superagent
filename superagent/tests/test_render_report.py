# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for tools/render_report.py — the HTML → PDF report renderer.

Pure logic (assets, furniture parsing, byline, staleness) runs everywhere;
the end-to-end render tests need Playwright's Chromium and skip cleanly
when it is not installed.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest
import yaml

from superagent.tools.render_report import (
    RENDER_SH_NAME,
    STYLE_NAME,
    THEME_NAME,
    check_stale,
    ensure_assets,
    main,
    needs_render,
    parse_header_title,
    prepared_line,
    tune_path,
)

NOW = dt.datetime(2026, 8, 31, 15, 0, tzinfo=dt.UTC)


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


needs_chromium = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright Chromium not installed"
)


def _write_report_html(path: Path, *, paragraphs: int = 3) -> Path:
    body = "\n".join(f"<p>Paragraph {i} with some body text.</p>" for i in range(paragraphs))
    path.write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<link rel='stylesheet' href='./{STYLE_NAME}'>"
        f"<link rel='stylesheet' href='./{THEME_NAME}'>"
        "</head><body class='viz-root'><main>"
        "<h1>Garage Insulation Options<br>"
        "<span class='h1-subtitle'>Findings Report — Rev. 1</span></h1>"
        f"{body}"
        "<section><h2 id='s-conclusion'>Conclusion</h2><p>Done.</p></section>"
        "</main></body></html>",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------- pure logic


def test_parse_header_title_with_subtitle():
    html = (
        "<h1>Roof Repair &amp; Gutters<br>"
        "<span class=\"h1-subtitle\">Contractor Brief — Rev. 2</span></h1>"
    )
    assert parse_header_title(html) == "Roof Repair & Gutters — Contractor Brief — Rev. 2"


def test_parse_header_title_plain_h1():
    assert parse_header_title("<h1>Simple Title</h1><p>x</p>") == "Simple Title"


def test_parse_header_title_missing_h1():
    assert parse_header_title("<p>no heading</p>") == ""


def test_prepared_line_variants():
    line = prepared_line({"name": "Ada Example", "email": "ada@example.com"}, None, NOW)
    assert line.startswith("Prepared by Ada Example <ada@example.com> on ")
    assert prepared_line({"name": "Ada Example"}, None, NOW).startswith("Prepared by Ada Example on ")
    assert prepared_line({}, None, NOW).startswith("Rendered on ")
    assert prepared_line({"name": "Ada"}, "Prepared by the House Elf", NOW).startswith(
        "Prepared by the House Elf on "
    )


def test_ensure_assets_seeds_refreshes_and_preserves_user_layers(tmp_path, framework_dir):
    workspace = tmp_path / "ws"
    source = tmp_path / "reports" / "2026-08-31_demo.html"
    style, theme, tune = ensure_assets(source, framework_dir, workspace)
    assert style.is_file() and theme.is_file() and tune.is_file()
    assert tune.name == "2026-08-31_demo.tune.css"
    template = (framework_dir / "templates" / "reports" / "style.css").read_bytes()
    assert style.read_bytes() == template
    render_sh = source.parent / RENDER_SH_NAME
    assert render_sh.is_file()
    assert render_sh.stat().st_mode & 0o111  # executable

    # Managed layers are refreshed when drifted; user layers never overwritten.
    style.write_text("/* drifted */", encoding="utf-8")
    render_sh.write_text("# drifted", encoding="utf-8")
    theme.write_text(".viz-root { --accent: #123456; }", encoding="utf-8")
    tune.write_text("h2 { margin-top: 1em; }", encoding="utf-8")
    ensure_assets(source, framework_dir, workspace)
    assert style.read_bytes() == template
    expected_sh = (
        (framework_dir / "templates" / "reports" / "render.sh")
        .read_bytes()
        .replace(b"@@SUPERAGENT_REPO@@", str(framework_dir.parent.resolve()).encode("utf-8"))
    )
    assert render_sh.read_bytes() == expected_sh
    assert theme.read_text(encoding="utf-8") == ".viz-root { --accent: #123456; }"
    assert tune.read_text(encoding="utf-8") == "h2 { margin-top: 1em; }"


def test_ensure_assets_uses_custom_overlay_theme(tmp_path, framework_dir):
    workspace = tmp_path / "ws"
    custom = workspace / "_custom" / "templates" / "reports" / "theme.css"
    custom.parent.mkdir(parents=True)
    custom.write_text(".viz-root { --accent: #7c3aed; }", encoding="utf-8")
    source = tmp_path / "reports" / "r.html"
    _, theme, _ = ensure_assets(source, framework_dir, workspace)
    assert theme.read_text(encoding="utf-8") == ".viz-root { --accent: #7c3aed; }"


def test_needs_render_staleness(tmp_path):
    html = tmp_path / "r.html"
    pdf = tmp_path / "r.pdf"
    html.write_text("<h1>x</h1>", encoding="utf-8")
    assert needs_render(html, pdf)  # missing PDF
    pdf.write_bytes(b"%PDF-fake")
    past = html.stat().st_mtime - 60
    os.utime(html, (past, past))
    assert not needs_render(html, pdf)  # fresh
    future = pdf.stat().st_mtime + 60
    os.utime(html, (future, future))
    assert needs_render(html, pdf)  # source newer


# ---------------------------------------------------------------- CLI errors


def test_main_missing_input(tmp_path, framework_dir, capsys):
    rc = main([str(tmp_path / "nope.html"), "--workspace", str(tmp_path / "ws")])
    assert rc == 1
    assert "no such file" in capsys.readouterr().err


def test_main_out_with_multiple_sources(tmp_path):
    a = _write_report_html(tmp_path / "a.html")
    b = _write_report_html(tmp_path / "b.html")
    rc = main([str(a), str(b), "--out", str(tmp_path / "x.pdf")])
    assert rc == 2


# ------------------------------------------------------------- end-to-end


@needs_chromium
def test_render_verify_and_lazy_skip(tmp_path, framework_dir, capsys):
    workspace = tmp_path / "ws"
    (workspace / "_memory").mkdir(parents=True)
    (workspace / "_memory" / "config.yaml").write_text(
        yaml.safe_dump({"profile": {"name": "Ada Example", "email": "ada@example.com"}}),
        encoding="utf-8",
    )
    html = _write_report_html(tmp_path / "2026-08-31_garage-insulation.html")
    argv = [str(html), "--workspace", str(workspace)]

    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "rendered" in out
    pdf = html.with_suffix(".pdf")
    assert pdf.is_file()
    from pypdf import PdfReader

    assert len(PdfReader(str(pdf)).pages) >= 1

    # Second run is a lazy no-op…
    stamp = pdf.stat().st_mtime_ns
    assert main(argv) == 0
    assert "fresh" in capsys.readouterr().out
    assert pdf.stat().st_mtime_ns == stamp

    # …and --check agrees, until the source moves ahead of the PDF.
    assert main([*argv, "--check"]) == 0
    future = pdf.stat().st_mtime + 60
    os.utime(html, (future, future))
    assert main([*argv, "--check"]) == 1
    assert main(argv) == 0  # stale → re-renders
    assert pdf.stat().st_mtime_ns > stamp


def _band_pixmap(page, rect):
    import fitz

    return page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, colorspace="gray")


def _band_ink(pix) -> int:
    return sum(1 for value in pix.samples if value < 200)


@needs_chromium
def test_render_multipage_has_two_pass_furniture(tmp_path, framework_dir):
    workspace = tmp_path / "ws"
    html = _write_report_html(tmp_path / "long.html", paragraphs=300)
    assert main([str(html), "--workspace", str(workspace), "--watermark", "TEST ONLY"]) == 0
    import fitz

    # Chromium draws header/footer templates as vector paths (not extractable
    # text), so assert on rasterized ink in the furniture bands instead.
    header = fitz.Rect(0, 0, 612, 55)
    footer_left = fitz.Rect(0, 750, 306, 792)
    with fitz.open(str(html.with_suffix(".pdf"))) as doc:
        assert len(doc) >= 2
        first_header = _band_pixmap(doc[0], header)
        second_header = _band_pixmap(doc[1], header)
        # Both pages carry header furniture…
        assert _band_ink(first_header) > 50
        assert _band_ink(second_header) > 50
        # …and the two passes differ (page-1 byline vs pages-2+ running title).
        assert first_header.samples != second_header.samples
        # Watermark ink in the footer-left band of every page.
        assert _band_ink(_band_pixmap(doc[0], footer_left)) > 50
        assert _band_ink(_band_pixmap(doc[1], footer_left)) > 50


@needs_chromium
def test_guard_flags_entirely_blank_page(tmp_path, capsys):
    html = tmp_path / "blank.html"
    html.write_text(
        f"<html><head><link rel='stylesheet' href='./{STYLE_NAME}'>"
        f"<link rel='stylesheet' href='./{THEME_NAME}'>"
        "<style>@media print { main { display: none } }</style></head>"
        "<body class='viz-root'><main><h1>Hidden</h1>"
        + "<p>text</p>" * 50
        + "</main></body></html>",
        encoding="utf-8",
    )
    rc = main([str(html), "--workspace", str(tmp_path / "ws"), "--strict"])
    captured = capsys.readouterr()
    assert "entirely blank" in captured.err
    assert rc == 1


@needs_chromium
def test_tune_edit_refires_render_and_survives(tmp_path, capsys):
    html = _write_report_html(tmp_path / "tuned.html")
    argv = [str(html), "--workspace", str(tmp_path / "ws")]
    assert main(argv) == 0
    tune = tune_path(html)
    assert tune.is_file()  # seeded by the render

    # Hand-tune → --check flags stale → re-render → the delta survives.
    tune.write_text(".viz-root { --fs-base: 10pt; }", encoding="utf-8")
    capsys.readouterr()
    assert main([*argv, "--check"]) == 1
    assert main(argv) == 0
    assert "rendered" in capsys.readouterr().out
    assert tune.read_text(encoding="utf-8") == ".viz-root { --fs-base: 10pt; }"


@needs_chromium
def test_render_sh_rebuilds_standalone(tmp_path):
    import subprocess

    html = _write_report_html(tmp_path / "shell.html")
    assert main([str(html), "--workspace", str(tmp_path / "ws")]) == 0
    render_sh = tmp_path / RENDER_SH_NAME
    assert render_sh.is_file()
    pdf = html.with_suffix(".pdf")
    stamp = pdf.stat().st_mtime_ns
    result = subprocess.run(
        ["sh", str(render_sh), "shell"], capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, result.stderr
    assert pdf.stat().st_mtime_ns > stamp  # forced rebuild happened


def test_subtitle_parsed_with_single_quotes():
    html = "<h1>T<br><span class='h1-subtitle'>Brief — Rev. 3</span></h1>"
    assert parse_header_title(html) == "T — Brief — Rev. 3"


def test_header_title_never_leaks_past_h1():
    # A plain H1 with a <br> later in the body must not swallow body text.
    html = "<h1>Plain Title</h1><p>SECRET first paragraph.</p><p>one<br>two</p>"
    assert parse_header_title(html) == "Plain Title"


def test_subtitle_before_br_not_duplicated():
    html = (
        "<h1>Deck Repair <span class=\"h1-subtitle\">Estimate — Rev. 4</span>"
        "<br>2026</h1>"
    )
    assert parse_header_title(html) == "Deck Repair — Estimate — Rev. 4"


def test_check_stale_mirrors_render_semantics(tmp_path, framework_dir):
    template = tmp_path / "template.css"
    template.write_text("/* base v1 */", encoding="utf-8")
    html = tmp_path / "r.html"
    html.write_text("<h1>x</h1>", encoding="utf-8")
    pdf = tmp_path / "r.pdf"
    style = tmp_path / STYLE_NAME
    theme = tmp_path / THEME_NAME
    tune = tune_path(html)
    assert tune.name == "r.tune.css"

    assert check_stale(html, pdf, style, theme, tune, template)  # no PDF yet

    pdf.write_bytes(b"%PDF-fake")
    past = pdf.stat().st_mtime - 60
    os.utime(html, (past, past))
    # Missing seeded siblings → a render would (re)create them → stale.
    assert check_stale(html, pdf, style, theme, tune, template)
    style.write_text("/* base v1 */", encoding="utf-8")
    assert check_stale(html, pdf, style, theme, tune, template)  # theme missing
    theme.write_text("/* theme */", encoding="utf-8")
    assert check_stale(html, pdf, style, theme, tune, template)  # tune missing
    tune.write_text("/* tune */", encoding="utf-8")
    for p in (style, theme, tune):
        os.utime(p, (past, past))
    assert not check_stale(html, pdf, style, theme, tune, template)  # fresh

    # An edited tune delta re-fires the render…
    future = pdf.stat().st_mtime + 60
    os.utime(tune, (future, future))
    assert check_stale(html, pdf, style, theme, tune, template)
    os.utime(tune, (past, past))

    # Template TOUCH without content change stays fresh (byte compare, not mtime)…
    os.utime(template, None)
    assert not check_stale(html, pdf, style, theme, tune, template)
    # …but a content drift is stale even with an old mtime.
    template.write_text("/* base v2 */", encoding="utf-8")
    os.utime(template, (past, past))
    assert check_stale(html, pdf, style, theme, tune, template)
