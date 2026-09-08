# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""0.20.0 retired the pull-on-demand reference model. Pin that it stays retired.

Deleted: the two `tools/sources_<cache|normalize>.py` modules (and their
tests), `templates/sources/ref.md` in its 0.19.0 reference shape (the watcher
template took the name), `.ref.txt` support, the `Sources/_cache/` reserved
name and the `preferences.sources.cache_*` / chunk / normalize config keys.
Nothing under `superagent/` may import the deleted modules; nothing outside
`tests/` may even mention them (test fixtures may quote legacy text).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

# Assembled at runtime so this file never contains the retired names verbatim
# (the scan below would otherwise flag itself).
RETIRED_MODULES = ("sources_" + "cache", "sources_" + "normalize")
IMPORT_RE = re.compile(
    r"^\s*(?:from\s+superagent\.tools(?:\.(\w+))?\s+import\s+([\w, ]+)|import\s+superagent\.tools\.(\w+))",
    re.MULTILINE,
)


def _python_files(framework_dir: Path) -> list[Path]:
    skip_parts = {"__pycache__", ".venv", ".venv.noSync"}
    return sorted(p for p in framework_dir.rglob("*.py") if not (set(p.parts) & skip_parts))


def test_retired_modules_and_tests_are_gone(framework_dir: Path) -> None:
    for name in RETIRED_MODULES:
        assert not (framework_dir / "tools" / f"{name}.py").exists(), f"tools/{name}.py must be deleted"
        assert not (framework_dir / "tests" / f"test_{name}.py").exists(), f"tests/test_{name}.py must be deleted"
    assert not (framework_dir / "templates" / "sources" / "watch.ref.md").exists()


def test_nothing_imports_the_retired_modules(framework_dir: Path) -> None:
    offenders: list[str] = []
    for path in _python_files(framework_dir):
        rel = path.relative_to(framework_dir)
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in IMPORT_RE.finditer(text):
            module, names, direct = m.group(1), m.group(2) or "", m.group(3)
            imported = {module, direct} | {n.strip() for n in names.split(",")}
            if imported & set(RETIRED_MODULES):
                offenders.append(f"{rel}: {m.group(0).strip()}")
        if rel.parts[0] == "tests":
            continue  # fixtures may quote legacy text; only imports count there
        for name in RETIRED_MODULES:
            if f"superagent.tools.{name}" in text or f"tools/{name}.py" in text or f"{name}." in text:
                offenders.append(f"{rel}: mentions {name}")
    assert offenders == [], "\n".join(offenders)


def test_sources_index_has_no_ref_txt_or_cache_config(framework_dir: Path) -> None:
    from superagent.tools import sources_index as si

    assert not hasattr(si, "REF_SUFFIXES")
    assert not hasattr(si, "DEFAULT_CACHE_REL")
    assert si.is_ref_file(Path("x.ref.md")) and not si.is_ref_file(Path("x.ref.txt"))
    assert si.ref_stem("Sources/notes/loose.ref.txt") is None
    assert si.is_meta_file(Path("manual.pdf.meta.md"))
    cfg = si.load_config(framework_dir.parent / "no-such-workspace")
    assert set(cfg) == {"auto_refresh_index", "watchlist_path"}


def test_config_template_sources_block_is_minimal(framework_dir: Path) -> None:
    data = yaml.safe_load((framework_dir / "templates" / "memory" / "config.yaml").read_text())
    sources = data["preferences"]["sources"]
    assert set(sources) == {"user_files_read_only", "auto_refresh_index"}, sources
    text = (framework_dir / "templates" / "memory" / "config.yaml").read_text()
    for key in ("cache_path:", "cache_max_mb:", "default_ttl_minutes:", "chunk_threshold_kb:",
                "chunk_target_kb:", "summary_first:", "normalize_policy"):
        assert not re.search(rf"^\s*{re.escape(key)}", text, re.MULTILINE), f"{key} still in config template"


def test_sources_index_template_documents_watcher_and_meta_rows(framework_dir: Path) -> None:
    text = (framework_dir / "templates" / "memory" / "sources-index.yaml").read_text()
    assert ".meta.md" in text and "watcher" in text
    assert "#   watch " in text, "the lifted `watch` mapping is documented"
    assert ".ref.txt" not in text
    assert "reference →" not in text and "reference ->" not in text
    assert "kind            — document | watcher" in text
