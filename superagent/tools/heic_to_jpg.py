#!/usr/bin/env -S uv run python
"""HEIC -> sRGB JPG conversion for Superagent imports.

Per `superagent/rules/image-format-policy.md`, Superagent never lands a
HEIC file under `workspace/Sources/`. Every HEIC the agent touches during
an import is converted to a sibling `.jpg` (sRGB, browser-renderable) and
the HEIC original is removed once the JPG is verified on disk.

The macOS-native `sips` path (`sips -s format jpeg src --out dest.jpg`)
silently produces an all-black JPG for Display P3 HEIC files captured on
recent iPhones (a documented colour-space bug; see the FasTrak IMG_3221
incident in the workspace history). To dodge it, this helper uses the
two-step `qlmanage` (Quick Look) HEIC -> PNG -> `sips` PNG -> JPG path,
which renders the visible image through Quick Look's own pipeline.

CLI:

    uv run python -m superagent.tools.heic_to_jpg convert <src.heic> [--keep]
    uv run python -m superagent.tools.heic_to_jpg convert-dir <dir> [--keep] [--dry-run]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HEIC_SUFFIXES: tuple[str, ...] = (".heic", ".heif")
QL_THUMBNAIL_SIZE = 2400


class ConversionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConversionResult:
    src: Path
    dest: Path
    created: bool  # True if dest was newly written (False if it already existed and we left it alone)
    removed_src: bool  # True if the HEIC was deleted after conversion


def _is_heic(path: Path) -> bool:
    return path.suffix.lower() in HEIC_SUFFIXES


def _jpg_sibling(src: Path) -> Path:
    return src.with_suffix(".jpg")


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise ConversionError(
            f"required tool `{name}` not found on PATH (HEIC conversion needs macOS sips + qlmanage)"
        )


def convert(src: Path, *, keep_heic: bool = False) -> ConversionResult:
    """Convert one HEIC file to a sibling .jpg via qlmanage -> sips.

    If a `.jpg` sibling already exists on disk, leave it alone (caller's
    JPG is assumed authoritative) and only remove the HEIC original
    (unless `keep_heic=True`). Returns a `ConversionResult` describing
    what was done.
    """
    src = src.resolve()
    if not src.is_file():
        raise ConversionError(f"source HEIC not found: {src}")
    if not _is_heic(src):
        raise ConversionError(f"not a HEIC/HEIF file: {src}")

    dest = _jpg_sibling(src)
    jpg_existed = dest.is_file()

    if not jpg_existed:
        _require_tool("qlmanage")
        _require_tool("sips")
        with tempfile.TemporaryDirectory(prefix="heic_to_jpg_") as tmp:
            tmp_dir = Path(tmp)
            # qlmanage emits <basename>.png alongside its input into -o <dir>
            png_path = tmp_dir / f"{src.name}.png"
            subprocess.run(
                ["qlmanage", "-t", "-s", str(QL_THUMBNAIL_SIZE), "-o", str(tmp_dir), str(src)],
                check=True,
                capture_output=True,
            )
            if not png_path.is_file():
                # Some qlmanage versions strip the original extension before
                # appending .png; check both forms before giving up.
                alt = tmp_dir / f"{src.stem}.png"
                if alt.is_file():
                    png_path = alt
                else:
                    produced = sorted(p.name for p in tmp_dir.iterdir())
                    raise ConversionError(
                        f"qlmanage did not produce a PNG for {src.name}; got {produced!r}"
                    )
            subprocess.run(
                ["sips", "-s", "format", "jpeg", str(png_path), "--out", str(dest)],
                check=True,
                capture_output=True,
            )
            if not dest.is_file():
                raise ConversionError(f"sips PNG->JPG did not write {dest}")

    removed_src = False
    if not keep_heic:
        src.unlink()
        removed_src = True

    return ConversionResult(src=src, dest=dest, created=not jpg_existed, removed_src=removed_src)


def convert_dir(
    root: Path,
    *,
    keep_heic: bool = False,
    dry_run: bool = False,
) -> list[ConversionResult]:
    """Walk `root` recursively, converting every HEIC/HEIF file."""
    root = root.resolve()
    if not root.is_dir():
        raise ConversionError(f"not a directory: {root}")
    results: list[ConversionResult] = []
    for src in sorted(p for p in root.rglob("*") if p.is_file() and _is_heic(p)):
        if dry_run:
            dest = _jpg_sibling(src)
            results.append(
                ConversionResult(
                    src=src,
                    dest=dest,
                    created=not dest.is_file(),
                    removed_src=not keep_heic,
                )
            )
            continue
        results.append(convert(src, keep_heic=keep_heic))
    return results


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="heic_to_jpg")
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="Convert a single HEIC/HEIF file to sibling .jpg.")
    c.add_argument("src", type=Path)
    c.add_argument(
        "--keep",
        action="store_true",
        help="Keep the HEIC original (default: delete after successful conversion).",
    )

    d = sub.add_parser(
        "convert-dir",
        help="Recursively convert every HEIC/HEIF under a directory.",
    )
    d.add_argument("root", type=Path)
    d.add_argument("--keep", action="store_true")
    d.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be converted without touching disk.",
    )
    return parser.parse_args(argv)


def _format(result: ConversionResult) -> str:
    action = "wrote" if result.created else "kept existing"
    suffix = " + removed HEIC" if result.removed_src else " (HEIC kept)"
    return f"{action} {result.dest}{suffix}"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        if args.cmd == "convert":
            result = convert(args.src, keep_heic=args.keep)
            print(_format(result))
            return 0
        if args.cmd == "convert-dir":
            results = convert_dir(args.root, keep_heic=args.keep, dry_run=args.dry_run)
            for result in results:
                tag = "DRY " if args.dry_run else ""
                print(f"{tag}{_format(result)}")
            print(f"converted {len(results)} file(s)")
            return 0
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        print(f"error: {exc.cmd[0]} exited {exc.returncode}: {stderr}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
