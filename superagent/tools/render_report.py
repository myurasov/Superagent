#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Render report HTML sources to paginated PDF via Chromium print.

The report pipeline (governed by `skills/report.md`): the agent authors a
self-contained semantic HTML file that links two sibling stylesheets —
`report-style.css` (framework-managed copy of `templates/reports/style.css`,
refreshed on every render) and `report-theme.css` (user-owned token
overrides, seeded once and never overwritten). This tool prints that HTML to
a US-Letter PDF using the Chromium bundled with Playwright (already installed
for browserctl) and owns ALL page furniture:

  header page 1     italic "Prepared by <profile.name> <email> on <ts>"
                    (from `_memory/config.yaml`; "Rendered on ..." fallback)
  header pages 2+   "<H1 title> — <h1-subtitle>" parsed from the HTML
  header right      "N / M" page numbers (every page)
  footer left       optional watermark (none by default)
  footer right      render date

Chromium cannot vary headers per page, so page 1 and pages 2+ are printed as
two passes over the same layout and merged with pypdf. A post-render guard
(re-authored from a sibling framework's page checker) warns on mixed page
sizes and large blank bands; `--strict` turns warnings into a failure.

Lazy by default: a PDF newer than its HTML source and both stylesheets is
skipped (`--force` re-renders; `--check` reports staleness without writing).

CLI:

    uv run python -m superagent.tools.render_report <report.html> [more.html ...]
    uv run python -m superagent.tools.render_report report.html --out out.pdf
    uv run python -m superagent.tools.render_report report.html --watermark "FAMILY ONLY"
    uv run python -m superagent.tools.render_report --check reports/*.html

No new dependencies: playwright (Chromium), pypdf (merge), pymupdf (guard)
are all existing project dependencies.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as html_lib
import io
import re
import sys
from pathlib import Path

import yaml

# Page geometry (inches) — matches the document style's US-Letter layout.
PAGE_MARGIN_TOP = 0.65
PAGE_MARGIN_BOTTOM = 0.65
PAGE_MARGIN_SIDE = 0.45
# Chromium's header/footer templates carry built-in root styles (default
# margins, 8px font) and don't reliably honor `in` units in shorthand
# padding, so furniture drifts off the body's left edge. Fix: zero the root
# and use px insets (96px = 1in) matching the page margins.
INSET_PX = round(PAGE_MARGIN_SIDE * 96)

FURNITURE_FONT_DEFAULT = "Helvetica Neue"

# Managed stylesheet names, siblings of each report HTML source.
STYLE_NAME = "report-style.css"
THEME_NAME = "report-theme.css"

THEME_STUB = """\
/* report-theme.css — user-owned token overrides for report styling.
   Seeded once by render_report; never overwritten. Override tokens on
   .viz-root, e.g.:
   .viz-root { --accent: #7c3aed; --font-sans: "Avenir Next", sans-serif; }
*/
"""

# Page-guard thresholds (re-authored from the sibling concept: a page whose
# largest internal or trailing blank band spans >= 30% of the content area
# usually indicates a pagination bug worth eyeballing).
GAP_SEVERE_FRACTION = 0.30
BLANK_ROW_INK = 0.002  # fraction of non-white pixels below which a row is blank
CHROME_TOP_FRACTION = 0.09  # skip the header band
CHROME_BOTTOM_FRACTION = 0.07  # skip the footer band


def workspace_default(framework: Path) -> Path:
    return framework.parent / "workspace"


def framework_style(framework: Path) -> Path:
    return framework / "templates" / "reports" / "style.css"


def load_profile(workspace: Path) -> dict:
    """Return the `profile:` block of `_memory/config.yaml` ({} on any failure)."""
    cfg_path = workspace / "_memory" / "config.yaml"
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    profile = (cfg or {}).get("profile")
    return profile if isinstance(profile, dict) else {}


def ensure_assets(directory: Path, framework: Path, workspace: Path) -> tuple[Path, Path]:
    """Materialize the two managed stylesheets next to a report source.

    `report-style.css` is a copy of the framework template, refreshed
    whenever the template's bytes differ (framework-managed layer).
    `report-theme.css` is seeded once — from
    `workspace/_custom/templates/reports/theme.css` when the user ships one,
    else from a commented stub — and never overwritten (user-owned layer).
    """
    directory.mkdir(parents=True, exist_ok=True)
    style_src = framework_style(framework)
    style_dst = directory / STYLE_NAME
    template_bytes = style_src.read_bytes()
    if not style_dst.exists() or style_dst.read_bytes() != template_bytes:
        style_dst.write_bytes(template_bytes)

    theme_dst = directory / THEME_NAME
    if not theme_dst.exists():
        custom = workspace / "_custom" / "templates" / "reports" / "theme.css"
        if custom.is_file():
            theme_dst.write_bytes(custom.read_bytes())
            print(f"Using _custom/templates/reports/theme.css (seeded {THEME_NAME})")
        else:
            theme_dst.write_text(THEME_STUB, encoding="utf-8")
    return style_dst, theme_dst


_TAG_RE = re.compile(r"<[^>]+>")
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL | re.IGNORECASE)
_SUBTITLE_SPAN_RE = re.compile(
    r"<span[^>]*class=[\"']h1-subtitle[\"'][^>]*>(.*?)</span>", re.DOTALL | re.IGNORECASE
)
_BR_RE = re.compile(r"<br", re.IGNORECASE)


def _strip(fragment: str) -> str:
    return html_lib.unescape(_TAG_RE.sub("", fragment)).strip()


def parse_header_title(html_text: str) -> str:
    """Pages-2+ running title: H1 main line + em-dash-joined subtitle.

    Both parses stay inside the first H1 block so body text can never leak
    into the running header; the subtitle span is cut out of the block
    before the main line is extracted so it is never emitted twice.
    """
    match = _H1_RE.search(html_text)
    if not match:
        return ""
    block = match.group(1)
    sub = _SUBTITLE_SPAN_RE.search(block)
    subtitle = ""
    if sub:
        subtitle = _strip(sub.group(1))
        block = block[: sub.start()] + block[sub.end() :]
    title = _strip(_BR_RE.split(block, maxsplit=1)[0])
    if subtitle:
        return f"{title} — {subtitle}" if title else subtitle
    return title


def prepared_line(profile: dict, override: str | None, now: dt.datetime) -> str:
    """The page-1 header byline. `override` wins; profile name/email next."""
    stamp = now.strftime("%b %d %Y, %H:%M %Z").strip()
    if override:
        return f"{override} on {stamp}"
    name = str(profile.get("name") or "").strip()
    email = str(profile.get("email") or "").strip()
    if name:
        who = f"{name} <{email}>" if email else name
        return f"Prepared by {who} on {stamp}"
    return f"Rendered on {stamp}"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def furniture_templates(
    *,
    prepared: str,
    header_title: str,
    watermark: str,
    date_text: str,
    furniture_font: str,
) -> tuple[str, str, str]:
    """Return (header_first, header_rest, footer) Chromium print templates."""
    safe_font = re.sub(r"[\"'<>;\\]", "", furniture_font)
    font_css = (
        f"font-family:'{safe_font}','Helvetica Neue',Helvetica,Arial,sans-serif;"
        "font-size:7.59pt;color:#ccc;"
    )
    box_css = (
        "width:100%;margin:0;display:flex;align-items:baseline;"
        f"padding-left:{INSET_PX}px;padding-right:{INSET_PX}px;{font_css}"
    )
    pages_right = (
        f'<span style="text-align:right;white-space:nowrap;{font_css}">'
        f'<span class="pageNumber" style="{font_css}"></span>'
        f'<span style="{font_css}"> / </span>'
        f'<span class="totalPages" style="{font_css}"></span></span>'
    )
    header_first = (
        f'<div style="{box_css}padding-top:12px;">'
        f'<span style="flex:1;text-align:left;font-style:italic;{font_css}">'
        f"{_esc(prepared)}</span>{pages_right}</div>"
    )
    header_rest = (
        f'<div style="{box_css}padding-top:12px;">'
        f'<span style="flex:1;text-align:left;{font_css}">{_esc(header_title)}</span>'
        f"{pages_right}</div>"
    )
    footer = (
        f'<div style="{box_css}padding-bottom:10px;">'
        f'<span style="flex:1;text-align:left;{font_css}">{_esc(watermark)}</span>'
        f'<span style="text-align:right;white-space:nowrap;{font_css}">'
        f"{_esc(date_text)}</span></div>"
    )
    return header_first, header_rest, footer


def needs_render(html_path: Path, pdf_path: Path, *extra_sources: Path) -> bool:
    """True when the PDF is missing or older than any source that shapes it."""
    if not pdf_path.exists():
        return True
    pdf_mtime = pdf_path.stat().st_mtime
    for source in (html_path, *extra_sources):
        if source.exists() and source.stat().st_mtime > pdf_mtime:
            return True
    return False


def check_stale(
    html_path: Path, pdf_path: Path, style: Path, theme: Path, style_template: Path
) -> bool:
    """Predict, without writing, whether a render run would re-render.

    Mirrors the render path exactly: `ensure_assets` would (re)write a
    missing or byte-drifted `report-style.css` and seed a missing
    `report-theme.css` — both with fresh mtimes — so those conditions are
    stale by definition; otherwise the plain mtime rule decides.
    """
    if not pdf_path.exists():
        return True
    if not style.exists() or style.read_bytes() != style_template.read_bytes():
        return True
    if not theme.exists():
        return True
    return needs_render(html_path, pdf_path, style, theme)


def render_pdf(
    html_path: Path,
    pdf_path: Path,
    *,
    prepared: str,
    header_title: str,
    watermark: str,
    furniture_font: str,
) -> int:
    """Print `html_path` to `pdf_path` (two furniture passes). Returns page count."""
    import contextlib

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader, PdfWriter

    now = dt.datetime.now().astimezone()
    header_first, header_rest, footer = furniture_templates(
        prepared=prepared,
        header_title=header_title,
        watermark=watermark,
        date_text=now.strftime("%b %d %Y"),
        furniture_font=furniture_font,
    )
    common = {
        "format": "Letter",
        "margin": {
            "top": f"{PAGE_MARGIN_TOP}in",
            "bottom": f"{PAGE_MARGIN_BOTTOM}in",
            "left": f"{PAGE_MARGIN_SIDE}in",
            "right": f"{PAGE_MARGIN_SIDE}in",
        },
        "print_background": True,
        "display_header_footer": True,
        "footer_template": footer,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            page.wait_for_timeout(300)  # let inline JS (diagrams) settle
            first = page.pdf(page_ranges="1", header_template=header_first, **common)
            rest: bytes | None = None
            with contextlib.suppress(PlaywrightError):  # single-page document
                rest = page.pdf(page_ranges="2-", header_template=header_rest, **common)
            page.close()
        finally:
            browser.close()

    writer = PdfWriter()
    writer.append(io.BytesIO(first))
    if rest:
        writer.append(io.BytesIO(rest))
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with pdf_path.open("wb") as fh:
        writer.write(fh)
    return len(PdfReader(str(pdf_path)).pages)


def verify_pdf(pdf_path: Path) -> list[str]:
    """Post-render guard: mixed page sizes + large blank bands (warnings)."""
    import fitz  # pymupdf

    warnings: list[str] = []
    with fitz.open(str(pdf_path)) as doc:
        sizes = {(round(p.rect.width, 1), round(p.rect.height, 1)) for p in doc}
        if len(sizes) > 1:
            warnings.append(f"mixed page sizes: {sorted(sizes)}")
        matrix = fitz.Matrix(0.5, 0.5)
        for index in range(len(doc)):
            pix = doc[index].get_pixmap(matrix=matrix, colorspace=fitz.csGRAY)
            band = _largest_blank_band(pix.samples, pix.width, pix.height, pix.stride)
            if band is None:
                continue
            kind, fraction = band
            if index == len(doc) - 1:
                # The final page may legitimately end early — but a page with
                # NO body ink at all is always a bug (e.g. print CSS hiding
                # the content).
                if fraction >= 0.999:
                    warnings.append(f"page {index + 1}: page is entirely blank")
                continue
            warnings.append(
                f"page {index + 1}: {kind} blank band spans "
                f"{fraction:.0%} of the content area"
            )
    return warnings


def _largest_blank_band(
    samples: bytes, width: int, height: int, stride: int
) -> tuple[str, float] | None:
    """Largest run of blank rows inside the content area, if severe."""
    top = int(height * CHROME_TOP_FRACTION)
    bottom = height - int(height * CHROME_BOTTOM_FRACTION)
    content_rows = bottom - top
    if content_rows <= 0 or width <= 0:
        return None
    blank_threshold = max(1, int(width * BLANK_ROW_INK))
    run = best = 0
    for y in range(top, bottom):
        row = samples[y * stride : y * stride + width]
        ink = sum(1 for value in row if value < 245)
        if ink < blank_threshold:
            run += 1
            best = max(best, run)
        else:
            run = 0
    trailing = run  # blank run touching the bottom of the content area
    if trailing >= content_rows * GAP_SEVERE_FRACTION:
        return ("trailing", trailing / content_rows)
    if best >= content_rows * GAP_SEVERE_FRACTION:
        return ("internal", best / content_rows)
    return None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="render_report")
    parser.add_argument("sources", nargs="+", type=Path, help="Report HTML file(s).")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output PDF path (single source only; default: sibling <stem>.pdf).",
    )
    parser.add_argument("--watermark", default="", help="Footer-left text (default: none).")
    parser.add_argument(
        "--prepared-by",
        default=None,
        help="Override the page-1 byline (default: profile name/email from config.yaml).",
    )
    parser.add_argument(
        "--no-prepared-by",
        action="store_true",
        help="Render without the page-1 byline (plain 'Rendered on ...').",
    )
    parser.add_argument(
        "--header-title",
        default=None,
        help="Override the pages-2+ running title (default: parsed from the H1).",
    )
    parser.add_argument(
        "--furniture-font",
        default=FURNITURE_FONT_DEFAULT,
        help=f"Header/footer font family (default: {FURNITURE_FONT_DEFAULT}).",
    )
    parser.add_argument("--force", action="store_true", help="Re-render even when fresh.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Don't render; exit 1 if any source is stale.",
    )
    parser.add_argument("--no-verify", action="store_true", help="Skip the page guard.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat page-guard warnings as errors (exit 1).",
    )
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument(
        "--framework",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework: Path = args.framework
    workspace: Path = args.workspace or workspace_default(framework)
    if args.out is not None and len(args.sources) > 1:
        print("error: --out is only valid with a single source", file=sys.stderr)
        return 2
    style_template = framework_style(framework)
    if not style_template.is_file():
        print(f"error: missing framework stylesheet {style_template}", file=sys.stderr)
        return 1

    profile = {} if args.no_prepared_by else load_profile(workspace)
    now = dt.datetime.now().astimezone()
    prepared = (
        prepared_line({}, None, now)
        if args.no_prepared_by
        else prepared_line(profile, args.prepared_by, now)
    )

    failures = 0
    stale = 0
    for source in args.sources:
        source = source.resolve()
        if not source.is_file():
            print(f"error: no such file {source}", file=sys.stderr)
            failures += 1
            continue
        pdf_path = (args.out or source.with_suffix(".pdf")).resolve()
        style = source.parent / STYLE_NAME
        theme = source.parent / THEME_NAME

        if args.check:
            if check_stale(source, pdf_path, style, theme, style_template):
                print(f"stale  {pdf_path}")
                stale += 1
            else:
                print(f"fresh  {pdf_path}")
            continue

        try:
            ensure_assets(source.parent, framework, workspace)
        except OSError as exc:
            print(f"error: could not manage stylesheets for {source}: {exc}", file=sys.stderr)
            failures += 1
            continue

        if not args.force and not needs_render(source, pdf_path, style, theme):
            print(f"fresh  {pdf_path} (skip; --force to re-render)")
            continue

        html_text = source.read_text(encoding="utf-8", errors="replace")
        if STYLE_NAME not in html_text:
            print(
                f"warning: {source.name} does not link ./{STYLE_NAME} — "
                "the PDF will render unstyled",
                file=sys.stderr,
            )
        header_title = (
            args.header_title if args.header_title is not None else parse_header_title(html_text)
        )

        try:
            pages = render_pdf(
                source,
                pdf_path,
                prepared=prepared,
                header_title=header_title,
                watermark=args.watermark,
                furniture_font=args.furniture_font,
            )
        except Exception as exc:  # noqa: BLE001 — every engine failure gets the same triage
            message = str(exc)
            if "Executable doesn't exist" in message or "playwright install" in message:
                print(
                    "error: Playwright's Chromium is not installed — run:\n"
                    "  uv run playwright install chromium",
                    file=sys.stderr,
                )
            else:
                print(f"error: render failed for {source.name}: {message}", file=sys.stderr)
            failures += 1
            continue

        print(f"rendered  {pdf_path} ({pages} page{'s' if pages != 1 else ''})")
        if not args.no_verify:
            warnings = verify_pdf(pdf_path)
            for warning in warnings:
                print(f"  guard: {warning}", file=sys.stderr)
            if warnings and args.strict:
                failures += 1

    if args.check and stale:
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
