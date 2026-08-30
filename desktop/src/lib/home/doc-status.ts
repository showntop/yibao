// 文档状态（wb-prototype focus.png 底栏）：字数口径——CJK 每字 1，拉丁/数字连续串 1，
// markdown 语法符号不计、代码块整体不计、链接只计文字。测试见 doc-status.test.ts。

export function docWordsOf(content: string): number {
  const text = content
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[#*_>`~]/g, " ");
  const cjk = text.match(/[㐀-䶿一-鿿]/g)?.length ?? 0;
  const latin = text.match(/[A-Za-z0-9][A-Za-z0-9'’-]*/g)?.length ?? 0;
  return cjk + latin;
}
