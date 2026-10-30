import { $$ } from "./dom";
import { EASE, reduced } from "./motion";

const AWAY: Keyframe[] = [
  { transform: "none", opacity: 1, filter: "blur(0)" },
  { transform: "translateY(-55%)", opacity: 0, filter: "blur(5px)" },
];
const IN: Keyframe[] = [
  { transform: "translateY(70%)", opacity: 0, filter: "blur(5px)" },
  { transform: "none", opacity: 1, filter: "blur(0)" },
];
const TURN_AWAY: Keyframe[] = [
  { transform: "perspective(400px)" },
  { transform: "perspective(400px) rotateY(90deg)" },
];
const TURN_IN: Keyframe[] = [
  { transform: "perspective(400px) rotateY(-90deg)" },
  { transform: "perspective(400px)" },
];

/**
 * Rolls a slot to new content: the old value rises away and the new one
 * rises in, piece (.it) by piece. Without a delay the new value just
 * replaces the old.
 */
export function roll(slot: HTMLElement, html: string, delay?: number) {
  const value = document.createElement("span");
  value.className = "v";
  value.innerHTML = html;
  const old = [...slot.children];
  slot.append(value);
  if (delay === undefined || reduced) {
    for (const v of old) v.remove();
    return;
  }
  for (const v of old)
    v.animate(AWAY, {
      duration: 460,
      delay,
      easing: EASE,
      fill: "both",
    }).onfinish = () => v.remove();
  $$(".it", value).forEach((piece, k) =>
    piece.animate(IN, {
      duration: 640,
      delay: delay + 80 + Math.min(k, 18) * 18,
      easing: EASE,
      fill: "both",
    }),
  );
}

/**
 * Turns a character cell to a new glyph, like the pleats of the name: the
 * old glyph turns edge-on and away, and the new one, laid over it, turns
 * in. Both are scheduled at once, so a busy page never strands a cell
 * edge-on. An empty cell just turns the new glyph in.
 */
export function flip(cell: HTMLElement, glyph: HTMLElement, delay: number) {
  const old = cell.lastElementChild;
  // a turn still under way gives way to this one
  for (const el of [...cell.children]) if (el !== old) el.remove();
  cell.append(glyph);
  if (reduced) {
    old?.remove();
    return;
  }
  if (old)
    old.animate(TURN_AWAY, {
      duration: 140,
      delay,
      easing: "cubic-bezier(0.5, 0, 0.75, 0)",
      fill: "forwards",
    }).onfinish = () => old.remove();
  glyph.animate(TURN_IN, {
    duration: 320,
    delay: delay + (old ? 140 : 0),
    easing: EASE,
    fill: "backwards",
  });
}
