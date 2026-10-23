const HIGHLIGHT_BUDGET = 100_000;

/** Stop counting as soon as the whole retained input/output exceeds the budget. */
export function canHighlightContent(value: unknown): boolean {
  let remaining = HIGHLIGHT_BUDGET;
  function visit(item: unknown, depth = 0): boolean {
    if (depth > 8) return false;
    if (typeof item === "string") remaining -= item.length;
    else if (Array.isArray(item)) {
      for (const child of item) if (!visit(child, depth + 1)) return false;
    } else if (item !== null && typeof item === "object") {
      for (const [key, child] of Object.entries(item)) {
        remaining -= key.length;
        if (remaining < 0 || !visit(child, depth + 1)) return false;
      }
    } else remaining -= String(item).length;
    return remaining >= 0;
  }
  return visit(value);
}
