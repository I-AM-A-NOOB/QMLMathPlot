"""The .qsb cache: location, explicit clear/info, and the automatic age/size prune."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest
import sympy as sp

import qmlmathplot.qsb as qsb
from qmlmathplot import model

X = sp.Symbol("x")


def _write(path: Path, age_days: float, size: int = 1024) -> None:
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))


def test_bake_uses_the_explicit_cache_and_cache_info_counts_it(tmp_path):
    vertex_source, _ = model.shader_sources(sp.sin(X))
    path = qsb.bake(vertex_source, "vert", cache_dir=tmp_path)
    assert path.parent == tmp_path and path.suffix == ".qsb"

    info = qsb.cache_info(tmp_path)
    assert info["files"] == 1 and info["bytes"] == path.stat().st_size
    assert info["path"] == str(tmp_path)

    assert qsb.bake(vertex_source, "vert", cache_dir=tmp_path) == path
    assert qsb.cache_info(tmp_path)["files"] == 1

    size = path.stat().st_size
    assert qsb.clear_cache(tmp_path) == size
    assert qsb.cache_info(tmp_path)["files"] == 0


def test_prune_removes_age_and_lru_entries_and_keeps_unrelated_files(tmp_path):
    old = tmp_path / "old.qsb"
    recent = tmp_path / "recent.qsb"
    middle = tmp_path / "middle.qsb"
    stale_tmp = tmp_path / "leftover.qsb.tmp"
    unrelated = tmp_path / "notes.txt"
    _write(old, 40.0, 1024)
    _write(recent, 1.0, 2_000_000)
    _write(middle, 10.0, 2_000_000)
    _write(stale_tmp, 2.0, 1024)
    unrelated.write_text("keep me", encoding="utf-8")

    freed = qsb.prune_cache(tmp_path, max_bytes=3_000_000, max_age_days=30.0)

    assert freed > 0
    assert not old.exists(), "an entry older than max_age_days must go"
    assert not stale_tmp.exists(), "an interrupted-bake leftover must go"
    assert not middle.exists(), "the LRU tail is used to get under the cap"
    assert recent.exists(), "the newest entry survives"
    assert unrelated.exists(), "the cache only owns its own files"
    assert sum(p.stat().st_size for p in tmp_path.glob("*.qsb")) <= 3_000_000


def test_bake_runs_automatic_prune_before_use(tmp_path, monkeypatch):
    seen: list[Path] = []
    monkeypatch.setattr(qsb, "_prune_if_due", lambda directory: seen.append(directory))
    vertex_source, _ = model.shader_sources(sp.sin(X))
    qsb.bake(vertex_source, "vert", cache_dir=tmp_path)
    assert seen and seen[0] == tmp_path


def test_default_cache_is_in_the_platform_temp_directory():
    if os.environ.get(qsb.CACHE_DIR_ENV):
        pytest.skip("cache directory overridden in the environment")
    assert qsb.cache_dir() == Path(tempfile.gettempdir()) / qsb.CACHE_NAME
