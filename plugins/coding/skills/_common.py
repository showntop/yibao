"""coding 插件内部共享：兄弟模块加载器（coding.py/sessions.py/transcript.py/_session_skills.py 共用）。

文件名以下划线开头 = 插件加载器跳过（不当 tool 模块加载）。R-35 归一：_sibling 的
加载逻辑收敛到本文件的 load_sibling，各入口文件只留薄委托（内联加载本文件后转发）；
公共件自身不含兄弟依赖，故无循环/无二次种子加载。

与 agents 插件 _common.load_sibling 的实现同构，但因目录不同各自维护一份
（跨插件共享需要跨目录 bootstrap 链，收益不成立）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_sibling(dir: Path, prefix: str, stem: str):
    """按路径加载同目录兄弟模块并缓存进 sys.modules：全插件共享同一实例。

    dir 传调用方所在目录（本模块 __file__ 指向 _common 自身，不能直接用）；
    prefix 为插件前缀（yibao_plugin_<plugin>）；先挂 sys.modules 再 exec：
    重复触发加载也拿到同一实例。
    """
    name = f"{prefix}_{stem}"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, dir / f"{stem}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod  # 先挂再 exec：重复触发加载也拿到同一实例
        spec.loader.exec_module(mod)
    return mod
