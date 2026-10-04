import { markColors, markGeometry } from "a13n-ui/brand/mark";
import { $$ } from "./dom";
import { reduced } from "./motion";

type Bar = (typeof markGeometry)["bar" | "cutout"];

const { center, viewBox } = markGeometry;
const bars = ({ x, y, width, height, rx }: Bar) =>
  markGeometry.angles
    .map(
      (angle) =>
        `<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="${rx}" transform="rotate(${angle} ${center} ${center})"/>`,
    )
    .join("");
const SHADOW = `<svg viewBox="${viewBox}"><g fill="${markColors.shadow}">${bars(markGeometry.bar)}</g></svg>`;
const FACE = `<svg viewBox="${viewBox}"><g fill="${markColors.face}">${bars(markGeometry.bar)}</g><g fill="${markColors.cutout}">${bars(markGeometry.cutout)}</g></svg>`;

/** The a13n mark, size px square: a shadow layer and a face layer that turn on their own. */
export function createMark(size: number) {
  const el = document.createElement("span");
  el.className = "mk";
  el.setAttribute("aria-hidden", "true");
  el.style.width = el.style.height = `${size}px`;
  el.innerHTML = SHADOW + FACE;
  return el;
}

/** Fills each [data-mark] slot with a mark of the size it names. */
export function mountMarks() {
  for (const slot of $$("[data-mark]"))
    slot.replaceWith(createMark(Number(slot.dataset.mark)));
}

const turns = new WeakMap<HTMLElement, number>();

/** One LogoAnimation turn: the face turns 60°, and the shadow follows with a little overshoot. */
export function turn(el: HTMLElement) {
  if (reduced) return;
  const deg = (turns.get(el) ?? 0) + 60;
  turns.set(el, deg);
  el.style.setProperty("--r", `${deg}deg`);
}
