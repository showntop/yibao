"""统一日志：sidecar 各模块的 [yibao] 行经此输出。

约定：stdout 是 stdio 协议通道（server.py 的 JSON 行），所有日志一律走 stderr。
后续如需级别/文件/结构化，收敛到本模块一处改造即可。
"""
import sys


def log(message: str) -> None:
    """打一条 [yibao] 前缀日志到 stderr。"""
    print(f"[yibao] {message}", file=sys.stderr)
