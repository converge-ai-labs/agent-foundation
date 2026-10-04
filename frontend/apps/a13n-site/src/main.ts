import "./styles/index.css";
import { cssVar, fontsReady } from "./dom";
import { Hero } from "./hero";
import { mountMarks } from "./mark";
import { Nav } from "./nav";
import type { Page } from "./page";

const RESIZE_SETTLE = 120; // ms

async function boot() {
  const display = cssVar("--display");
  // the hero measures its name with the display face
  await fontsReady(`700 100px ${display}`);

  mountMarks();
  const nav = new Nav();
  const hero = new Hero(display);
  let page: Page | null = null;

  let settle = 0;
  addEventListener("resize", () => {
    hero.layout();
    clearTimeout(settle);
    settle = window.setTimeout(() => page?.layout(), RESIZE_SETTLE);
  });

  let last = performance.now();
  const frame = (now: number) => {
    const dt = Math.min((now - last) / 1000, 1 / 30);
    last = now;
    hero.tick(now, dt);
    page?.tick(now, dt);
    nav.tick(page?.dark ?? false);
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);

  // the page below the hero loads once the hero is on screen
  page = await (await import("./page")).Page.mount();
}

void boot();
