import os
import sys

# 让 tests/ 下的共享模块（如 fakes）可被同目录 test 文件直接 import。
sys.path.insert(0, os.path.dirname(__file__))
