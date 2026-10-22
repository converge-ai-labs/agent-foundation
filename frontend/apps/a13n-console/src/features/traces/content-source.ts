export const TEXT_CHUNK_SIZE = 20_000;

/** Prefer line boundaries; preserve every character, including surrogate pairs. */
export function sourceChunks(text: string): string[] {
  const chunks: string[] = [];
  for (let start = 0; start < text.length;) {
    let end = Math.min(start + TEXT_CHUNK_SIZE, text.length);
    if (end < text.length) {
      const newline = text.slice(start, end).lastIndexOf("\n");
      if (newline >= TEXT_CHUNK_SIZE / 2) end = start + newline + 1;
      else if (
        /[\uD800-\uDBFF]/.test(text[end - 1]!) &&
        /[\uDC00-\uDFFF]/.test(text[end]!)
      )
        end--;
    }
    chunks.push(text.slice(start, end));
    start = end;
  }
  return chunks;
}
