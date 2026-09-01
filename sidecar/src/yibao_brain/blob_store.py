"""本地 content-addressed BlobStore。

Tool 先把内容写入 staging，再在 PluginDb 事务提交前原子 promote 到 objects。
这样崩溃最多留下无引用 blob，不会留下已提交却不存在的 content_ref；无引用对象
经过宽限期后由 Host 依据 Work Graph 引用集合回收。
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


_BLOB_REF = re.compile(r"^blob://sha256/([0-9a-f]{64})$")


@dataclass
class StagedBlob:
    store: "BlobStore"
    digest: str
    size: int
    staging_path: Path
    _done: bool = False

    @property
    def ref(self) -> str:
        return f"blob://sha256/{self.digest}"

    def finalize(self) -> str:
        if not self._done:
            self.store._finalize(self)
            self._done = True
        return self.ref

    def discard(self) -> None:
        if self._done:
            return
        self.staging_path.unlink(missing_ok=True)
        self._done = True


class BlobStore:
    """进程共享的大内容存储；引用由内容哈希决定，与插件和绝对路径解耦。"""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.staging_dir = self.root / "staging"
        self.objects_dir = self.root / "objects" / "sha256"
        self.staging_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.objects_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def digest_from_ref(ref: str) -> str:
        match = _BLOB_REF.fullmatch(str(ref).strip())
        if match is None:
            raise ValueError(f"非法 BlobRef：{ref!r}")
        return match.group(1)

    def path_for_digest(self, digest: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"非法 sha256：{digest!r}")
        return self.objects_dir / digest[:2] / digest

    def resolve(self, ref: str, *, require_exists: bool = True) -> Path:
        path = self.path_for_digest(self.digest_from_ref(ref))
        if require_exists and not path.is_file():
            raise FileNotFoundError(f"Blob 不存在：{ref}")
        return path

    def stage_bytes(self, data: bytes | bytearray | memoryview) -> StagedBlob:
        return self._stage_chunks((bytes(data),))

    def stage_text(self, text: str, *, encoding: str = "utf-8") -> StagedBlob:
        return self.stage_bytes(str(text).encode(encoding))

    def stage_file(self, path: str | os.PathLike[str], *, chunk_size: int = 1024 * 1024) -> StagedBlob:
        source = Path(path)

        def chunks() -> Iterator[bytes]:
            with source.open("rb") as handle:
                while chunk := handle.read(chunk_size):
                    yield chunk

        return self._stage_chunks(chunks())

    def _stage_chunks(self, chunks: Iterable[bytes]) -> StagedBlob:
        fd, raw_path = tempfile.mkstemp(prefix="blob-", suffix=".stage", dir=self.staging_dir)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        chunk = bytes(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            Path(raw_path).unlink(missing_ok=True)
            raise
        return StagedBlob(self, digest.hexdigest(), size, Path(raw_path))

    def _finalize(self, staged: StagedBlob) -> Path:
        if staged.store is not self:
            raise ValueError("StagedBlob 不属于当前 BlobStore")
        target = self.path_for_digest(staged.digest)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.is_file():
            staged.staging_path.unlink(missing_ok=True)
        else:
            os.replace(staged.staging_path, target)
            os.chmod(target, 0o600)
            try:
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        return target

    def gc_orphans(
        self, live_refs: Iterable[str], *, grace_seconds: float = 7 * 24 * 3600,
        now: float | None = None,
    ) -> dict[str, int]:
        """回收过期 staging 与无 Work Graph 引用的对象；宽限期避免竞态。"""
        current = time.time() if now is None else float(now)
        live: set[str] = set()
        for ref in live_refs:
            try:
                live.add(self.digest_from_ref(ref))
            except ValueError:
                continue
        removed_staging = 0
        removed_objects = 0
        for path in self.staging_dir.glob("*.stage"):
            try:
                if path.is_file() and current - path.stat().st_mtime >= grace_seconds:
                    path.unlink()
                    removed_staging += 1
            except OSError:
                continue
        for prefix in self.objects_dir.iterdir():
            if not prefix.is_dir() or not re.fullmatch(r"[0-9a-f]{2}", prefix.name):
                continue
            for path in prefix.iterdir():
                try:
                    if (
                        path.is_file()
                        and re.fullmatch(r"[0-9a-f]{64}", path.name)
                        and path.name not in live
                        and current - path.stat().st_mtime >= grace_seconds
                    ):
                        path.unlink()
                        removed_objects += 1
                except OSError:
                    continue
            try:
                prefix.rmdir()
            except OSError:
                pass
        return {"staging": removed_staging, "objects": removed_objects}
