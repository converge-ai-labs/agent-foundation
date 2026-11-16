const titleSegments = new Intl.Segmenter(undefined, {
  granularity: "grapheme",
});

export function workspaceDocumentTitle(title: string): string {
  const segments = Array.from(
    titleSegments.segment(title),
    ({ segment }) => segment,
  );
  const preview =
    segments.length > 80
      ? `${segments.slice(0, 79).join("").trimEnd()}…`
      : title;
  return `${preview} - Harness UI`;
}
