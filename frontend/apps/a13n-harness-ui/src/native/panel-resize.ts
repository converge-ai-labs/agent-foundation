/** Snap before either column becomes too narrow, keeping ordinary drags direct. */
export function panelResize(width: number, available: number) {
  const edge = Math.min(35, Math.max(25, (280 / available) * 100));
  if (width <= edge) return { kind: "close" } as const;
  if (width >= 100 - edge) return { kind: "tabs" } as const;
  return { kind: "resize", width } as const;
}
