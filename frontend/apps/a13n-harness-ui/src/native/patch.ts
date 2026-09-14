export type PatchLine = {
  kind: "context" | "addition" | "deletion" | "hunk" | "metadata";
  oldLine?: number;
  newLine?: number;
};

// The document is always the exact reviewed patch. File gutters are annotations,
// never a reconstructed file or a replacement coordinate system for capture.
export function patchLines(text: string): PatchLine[] {
  let oldLine = 0;
  let newLine = 0;
  let oldRemaining = 0;
  let newRemaining = 0;
  return text.split(/\r\n|\r|\n/).map((line): PatchLine => {
    const hunk = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(line);
    if (hunk) {
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[3]);
      oldRemaining = Number(hunk[2] ?? 1);
      newRemaining = Number(hunk[4] ?? 1);
      return { kind: "hunk" };
    }
    if (line.startsWith("\\")) return { kind: "metadata" };
    if (line.startsWith("-") && oldRemaining > 0) {
      oldRemaining--;
      return { kind: "deletion", oldLine: oldLine++ };
    }
    if (line.startsWith("+") && newRemaining > 0) {
      newRemaining--;
      return { kind: "addition", newLine: newLine++ };
    }
    if (line.startsWith(" ") && oldRemaining > 0 && newRemaining > 0) {
      oldRemaining--;
      newRemaining--;
      return { kind: "context", oldLine: oldLine++, newLine: newLine++ };
    }
    // Unknown/combined diff headers remain readable, without invented file lines.
    oldRemaining = newRemaining = 0;
    return { kind: "metadata" };
  });
}
