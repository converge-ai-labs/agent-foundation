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
