from __future__ import annotations

import os

import pytest

from yibao_brain.blob_store import BlobStore


def test_stage_finalize_deduplicates_and_resolves_content(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    first = store.stage_text("同一份正文")
    assert not store.resolve(first.ref, require_exists=False).exists()
    ref = first.finalize()
    path = store.resolve(ref)
    assert path.read_text(encoding="utf-8") == "同一份正文"

    second = store.stage_text("同一份正文")
    assert second.ref == ref
    second.finalize()
    assert store.resolve(ref) == path
    assert list(store.staging_dir.glob("*.stage")) == []


def test_gc_removes_only_expired_unreferenced_objects_and_staging(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    live = store.stage_text("仍被引用")
    live_ref = live.finalize()
    orphan = store.stage_text("已经失去引用")
    orphan_ref = orphan.finalize()
    abandoned = store.stage_text("提交前崩溃")

    old = 100.0
    for path in (store.resolve(live_ref), store.resolve(orphan_ref), abandoned.staging_path):
        os.utime(path, (old, old))
    result = store.gc_orphans([live_ref], grace_seconds=10, now=old + 11)
    assert result == {"staging": 1, "objects": 1}
    assert store.resolve(live_ref).is_file()
    assert not store.resolve(orphan_ref, require_exists=False).exists()


def test_blob_ref_validation_blocks_path_escape(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    with pytest.raises(ValueError, match="非法 BlobRef"):
        store.resolve("blob://sha256/../../etc/passwd")
