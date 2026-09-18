export type ReadingAnchor = { id: string; turn?: string; offset: number };

export function captureReadingAnchor(
  reader: HTMLElement,
): ReadingAnchor | undefined {
  const top = reader.getBoundingClientRect().top;
  const row = [
    ...reader.querySelectorAll<HTMLElement>("[data-reading-anchor]"),
  ].find((element) => {
    // Nested execution readers own their anchors; their clipped rows must not
    // become anchors for the surrounding conversation.
    if (
      element.closest("[data-execution-reader]") !==
      reader.closest("[data-execution-reader]")
    )
      return false;
    const rect = element.getBoundingClientRect();
    return rect.height > 0 && rect.bottom > top;
  });
  return row
    ? {
        id: row.dataset.readingAnchor!,
        turn: row.closest<HTMLElement>("[data-turn-id]")?.dataset.turnId,
        offset: row.getBoundingClientRect().top - top,
      }
    : undefined;
}

export function restoreReadingAnchor(
  reader: HTMLElement,
  anchor?: ReadingAnchor,
) {
  if (!anchor) return;
  let target = [
    ...reader.querySelectorAll<HTMLElement>("[data-reading-anchor]"),
  ].find(
    (element) =>
      element.dataset.readingAnchor === anchor.id &&
      element.closest("[data-execution-reader]") ===
        reader.closest("[data-execution-reader]"),
  );
  let offset = anchor.offset;
  if (!target || target.getBoundingClientRect().height === 0) {
    target = [...reader.querySelectorAll<HTMLElement>("[data-turn-id]")].find(
      (element) => element.dataset.turnId === anchor.turn,
    );
    offset = 16;
  }
  if (target)
    reader.scrollTop +=
      target.getBoundingClientRect().top -
      reader.getBoundingClientRect().top -
      offset;
}
