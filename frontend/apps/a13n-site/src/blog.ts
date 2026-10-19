import "./styles/blog.css";
import { $$ } from "./dom";
import { mountMarks } from "./mark";
import { mountStars } from "./stars";

const LABEL = 12; // px, a plate label on screen
const LABEL_MAX = 16; // drawing units; larger labels collide with the drawing
const CROP_PAD = 10; // drawing units around a cropped cover

mountMarks();
mountStars();

/** Copies a value and confirms on success. */
async function copy(value: string, done: () => void) {
  try {
    await navigator.clipboard.writeText(value);
    done();
  } catch {
    // without clipboard access the control stays as it was
  }
}

for (const button of $$(".cb-copy")) {
  button.addEventListener("click", () =>
    copy(button.closest(".codeblock")!.querySelector("pre")!.innerText, () => {
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = "Copy"), 1200);
    }),
  );
}

// a heading's link also copies its address
for (const anchor of $$<HTMLAnchorElement>(".prose .anchor")) {
  anchor.addEventListener("click", () =>
    copy(anchor.href, () => {
      anchor.classList.add("copied");
      setTimeout(() => anchor.classList.remove("copied"), 1200);
    }),
  );
}

// plate labels hold one size on screen, or hide where they would crowd the drawing
const plates = new ResizeObserver((entries) => {
  for (const { target } of entries) {
    const scale = target.querySelector("svg")?.getScreenCTM()?.a;
    if (!scale) continue;
    const size = LABEL / scale;
    target.classList.toggle("bare", size > LABEL_MAX);
    (target as HTMLElement).style.setProperty("--pt", `${size}px`);
  }
});
for (const plate of $$(".plate:not(.bare)")) plates.observe(plate);

// the cover crops its drawing to the lines, leaving the labels out
for (const svg of $$<SVGSVGElement>(".cover svg")) {
  const boxes = $$<SVGGraphicsElement>(":scope > :not(text)", svg).map((el) =>
    el.getBBox(),
  );
  const x = Math.min(...boxes.map((b) => b.x)) - CROP_PAD;
  const y = Math.min(...boxes.map((b) => b.y)) - CROP_PAD;
  const right = Math.max(...boxes.map((b) => b.x + b.width)) + CROP_PAD;
  const bottom = Math.max(...boxes.map((b) => b.y + b.height)) + CROP_PAD;
  svg.setAttribute("viewBox", `${x} ${y} ${right - x} ${bottom - y}`);
}

// a scrollable figure fades its trailing edge until it is scrolled to the end
for (const box of $$(".fig-scroll")) {
  const update = () =>
    box.classList.toggle(
      "end",
      box.scrollLeft + box.clientWidth >= box.scrollWidth - 2,
    );
  box.addEventListener("scroll", update, { passive: true });
  new ResizeObserver(update).observe(box);
}

// a hairline under the nav fills as the post is read
const progress = document.querySelector<HTMLElement>(".progress");
const body = document.querySelector<HTMLElement>(".prose");
if (progress && body) {
  const update = () => {
    const r = body.getBoundingClientRect();
    const read = Math.min(
      1,
      Math.max(0, (innerHeight * 0.5 - r.top) / r.height),
    );
    progress.style.transform = `scaleX(${read})`;
  };
  addEventListener("scroll", update, { passive: true });
  update();
}
