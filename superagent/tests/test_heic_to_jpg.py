# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""HEIC -> JPG conversion: the macOS and the portable path.

The macOS path shells out to Quick Look and `sips`; the portable path decodes
with Pillow and `pillow-heif` (a Linux-only dependency), so its end-to-end
test runs only where that package is installed.
"""

from __future__ import annotations

import sys

import pytest

from superagent.tools import heic_to_jpg as h


def _fake_heic(tmp_path, name: str = "IMG_0001.HEIC"):
    src = tmp_path / name
    src.write_bytes(b"not a real heic")
    return src


def test_convert_uses_quicklook_on_macos_and_pillow_elsewhere(tmp_path, monkeypatch):
    used: list[str] = []

    def fake(label):
        def run(src, dest):
            used.append(label)
            dest.write_bytes(b"jpg")
        return run

    monkeypatch.setattr(h, "_convert_with_quicklook", fake("quicklook"))
    monkeypatch.setattr(h, "_convert_with_pillow", fake("pillow"))

    monkeypatch.setattr(h.sys, "platform", "darwin")
    result = h.convert(_fake_heic(tmp_path, "a.heic"))
    assert result.created and result.removed_src and result.dest.name == "a.jpg"

    monkeypatch.setattr(h.sys, "platform", "linux")
    h.convert(_fake_heic(tmp_path, "b.heif"))
    assert used == ["quicklook", "pillow"]


def test_existing_jpg_is_kept_and_only_the_heic_is_removed(tmp_path, monkeypatch):
    def boom(src, dest):
        raise AssertionError("no conversion expected")

    monkeypatch.setattr(h, "_convert_with_quicklook", boom)
    monkeypatch.setattr(h, "_convert_with_pillow", boom)
    src = _fake_heic(tmp_path, "c.heic")
    jpg = tmp_path / "c.jpg"
    jpg.write_bytes(b"authoritative")
    result = h.convert(src)
    assert not result.created and result.removed_src
    assert jpg.read_bytes() == b"authoritative" and not src.exists()


def test_keep_flag_leaves_the_heic_in_place(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "_convert_with_quicklook", lambda src, dest: dest.write_bytes(b"jpg"))
    monkeypatch.setattr(h, "_convert_with_pillow", lambda src, dest: dest.write_bytes(b"jpg"))
    src = _fake_heic(tmp_path, "d.heic")
    result = h.convert(src, keep_heic=True)
    assert not result.removed_src and src.exists()


def test_non_heic_and_missing_sources_are_refused(tmp_path):
    with pytest.raises(h.ConversionError, match="not found"):
        h.convert(tmp_path / "missing.heic")
    other = tmp_path / "photo.png"
    other.write_bytes(b"x")
    with pytest.raises(h.ConversionError, match="not a HEIC"):
        h.convert(other)


def test_portable_path_names_the_missing_package(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pillow_heif", None)  # makes the import fail
    src = _fake_heic(tmp_path, "e.heic")
    with pytest.raises(h.ConversionError, match="pillow-heif"):
        h._convert_with_pillow(src, tmp_path / "e.jpg")
    assert not (tmp_path / "e.jpg").exists()


def test_portable_path_reports_an_undecodable_file(tmp_path):
    pytest.importorskip("pillow_heif")
    src = _fake_heic(tmp_path, "f.heic")
    with pytest.raises(h.ConversionError, match="could not decode"):
        h._convert_with_pillow(src, tmp_path / "f.jpg")
    assert not (tmp_path / "f.jpg").exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_portable_path_writes_an_srgb_jpg(tmp_path, monkeypatch):
    pillow_heif = pytest.importorskip("pillow_heif")
    from PIL import Image

    pillow_heif.register_heif_opener()
    src = tmp_path / "g.heic"
    try:
        Image.new("RGB", (64, 48), (200, 30, 30)).save(src, format="HEIF")
    except Exception as exc:  # a build without an HEVC encoder cannot make the fixture
        pytest.skip(f"cannot encode a HEIC fixture here: {exc}")

    monkeypatch.setattr(h.sys, "platform", "linux")
    result = h.convert(src)
    assert result.created and result.removed_src and not src.exists()
    with Image.open(result.dest) as out:
        assert out.format == "JPEG" and out.mode == "RGB" and out.size == (64, 48)
        assert out.info.get("icc_profile")
        r, g, b = out.getpixel((32, 24))
        assert r > 150 and g < 90 and b < 90
