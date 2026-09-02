"""zimeiti 字数唯一权威口径（2026-09-02，9-01 报告 P1-08 三套口径统一）。

文档总字数 doc_words：与桌面 Focus 编辑器 desktop/src/lib/home/doc-status.ts 的
docWordsOf 同一口径（改动必须两侧同步）——
- fenced 代码块（```…```）整体不计；[文字](链接)/![alt](图) 只计文字；
- #*_>`~ 记号与空白不计；
- CJK（㐀-䶿 一-鿿）每字计 1，拉丁/数字连续串（内部可含 '’-）每串计 1。

口播字数 narration_words：现有稿件格式（skills/write 五段式 markdown）没有口播段
标记——口播按镜拆分在 storyboards.narration，属分镜域不进稿件——故稿件口播=全文，
不发明格式。

文件名以 _ 开头 = 插件加载器跳过（不当 tool 模块加载）；兄弟模块经各 tool 文件里的
_load_wordcount() 按路径加载并缓存进 sys.modules（仿 coding/agents 的 _sibling 先例，
全插件共享同一实例）。
"""
from __future__ import annotations

import re

_CODE_FENCE = re.compile(r"```[\s\S]*?```")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"[#*_>`~]")
_CJK = re.compile(r"[㐀-䶿一-鿿]")
_LATIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")


def doc_words(content: str) -> int:
    """文档总字数：去空白/markdown 标记后，CJK 按字 + 拉丁/数字连续串按词。"""
    text = _CODE_FENCE.sub(" ", str(content or ""))
    text = _LINK.sub(r"\1", text)
    text = _MD_MARKS.sub(" ", text)
    return len(_CJK.findall(text)) + len(_LATIN.findall(text))


def narration_words(content: str) -> int:
    """口播字数：稿件无口播段标记（以 skills/write 稿件格式事实为准），口播 = 全文。"""
    return doc_words(content)
