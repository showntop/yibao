import os
import sys

import pytest

# 让 tests/ 下的共享模块（如 fakes）可被同目录 test 文件直接 import。
sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture(autouse=True)
def isolate_yibao_data_dir(tmp_path, monkeypatch):
    """Every test gets a disposable app-data root unless it explicitly overrides it.

    Server integration tests construct the real WorkGraphStore. Without this guard they
    can write fake tool invocations into the user's Application Support database.
    """
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path / "yibao-data"))
