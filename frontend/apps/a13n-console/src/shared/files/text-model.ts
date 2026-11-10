/** Preserve raw line endings in the editor model, including mixed CRLF/LF. */
export function sourceLines(text: string): string[] {
  return text.split("\n");
}

export function textFormat(text: string): {
  endings: "CRLF" | "LF" | "Mixed" | "None";
  finalNewline: boolean;
} {
  const crlf = text.match(/\r\n/g)?.length ?? 0;
  const lf = text.match(/\n/g)?.length ?? 0;
  return {
    endings: crlf && crlf !== lf ? "Mixed" : crlf ? "CRLF" : lf ? "LF" : "None",
    finalNewline: text.endsWith("\n"),
  };
}
