/** 整桌「一句」：娱乐插件 fun.quote 的脸，不编诗。 */

export type LineFace = {
  text: string;
  from: string;
};

export const LINE_KEY = "yibao-line";

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export function lineFace(data: unknown): LineFace | null {
  const root = asRecord(data);
  const rows = root && Array.isArray(root.rows) ? root.rows : [];
  const first = asRecord(rows[0]);
  if (!first) return null;
  const text = String(first.text ?? "").trim();
  if (!text) return null;
  return { text, from: String(first.from ?? "").trim() };
}

export function readLineCache(storage: Pick<Storage, "getItem"> | null): LineFace | null {
  try {
    const raw = storage?.getItem(LINE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { text?: unknown; from?: unknown };
    const text = String(parsed.text ?? "").trim();
    if (!text) return null;
    return { text, from: String(parsed.from ?? "").trim() };
  } catch {
    return null;
  }
}

export function writeLineCache(face: LineFace, storage: Pick<Storage, "setItem"> | null): void {
  try { storage?.setItem(LINE_KEY, JSON.stringify(face)); } catch { /* ignore quota */ }
}
