/** The element a selector must find. */
export function $<T extends Element = HTMLElement>(
  selector: string,
  root: ParentNode = document,
): T {
  const el = root.querySelector<T>(selector);
  if (!el) throw new Error(`No element matches ${selector}`);
  return el;
}

export function $$<T extends Element = HTMLElement>(
  selector: string,
  root: ParentNode = document,
): T[] {
  return [...root.querySelectorAll<T>(selector)];
}

export function context2d(canvas: HTMLCanvasElement) {
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D is not available");
  return ctx;
}

/** A custom property of the page, such as one of its font stacks. */
export function cssVar(name: string) {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
}

/** Resolves once these faces can measure and draw; a failed load keeps the fallback. */
export function fontsReady(...fonts: string[]) {
  return Promise.all(fonts.map((font) => document.fonts.load(font))).then(
    () => {},
    () => {},
  );
}
