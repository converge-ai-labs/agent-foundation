import { $ } from "./dom";
import { EASE, reduced } from "./motion";
import { flip } from "./roll";

const KEY = "a13n.stars";
const TURN = 72; // deg, one point of the star
const COUNT = /^\d[\d.]*[kMB]?$/; // 187, 1.2k, 251k

// the Octicons star
const STAR = `<svg class="star" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 .416 1.279l-3.046 2.97.719 4.192a.751.751 0 0 1-1.088.791L8 12.347l-3.766 1.98a.75.75 0 0 1-1.088-.79l.72-4.194L.818 6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75.75 0 0 1 8 .25Z"/></svg>`;

function load() {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function save(count: string) {
  try {
    localStorage.setItem(KEY, count);
  } catch {
    // without storage the count turns in on every visit
  }
}

/**
 * The star count of a repository path such as /owner/name, as Shields
 * formats it, or null. Shields caches GitHub for everyone, so visitors
 * never meet GitHub's per-address limit.
 */
async function fetchCount(repo: string) {
  try {
    const res = await fetch(`https://img.shields.io/github/stars${repo}.json`);
    const { value } = await res.json();
    return COUNT.test(value) ? (value as string) : null;
  } catch {
    return null;
  }
}

/**
 * Shows a count beside the link. With turn, the count makes room for
 * itself and only the digits that changed turn in, in a wave.
 */
function show(
  link: HTMLElement,
  box: HTMLElement,
  count: string,
  turn: boolean,
) {
  const from = box.isConnected ? box.offsetWidth : 0;
  if (!box.isConnected) link.append(box);
  const digits = $(".digits", box);
  $(".vh", box).textContent = `, ${count} stars`;
  if (digits.childElementCount !== count.length)
    digits.replaceChildren(
      ...[...count].map(() => document.createElement("span")),
    );
  [...count].forEach((ch, i) => {
    const cell = digits.children[i] as HTMLElement;
    if (cell.lastElementChild?.textContent === ch) return;
    const glyph = document.createElement("span");
    glyph.textContent = ch;
    if (turn) flip(cell, glyph, 120 + i * 50);
    else cell.replaceChildren(glyph);
  });
  const to = box.offsetWidth;
  if (turn && !reduced && from !== to)
    box.animate([{ width: `${from}px` }, { width: `${to}px` }], {
      duration: 700,
      easing: EASE,
    });
}

/**
 * The GitHub link marked data-stars shows the repository's star count.
 * The count from the last visit shows at once; a new one turns in.
 * Without a count the link reads just GitHub.
 */
export function mountStars() {
  const link = $<HTMLAnchorElement>("a[data-stars]");
  const box = document.createElement("span");
  box.className = "stars";
  box.innerHTML = `${STAR}<span class="digits" aria-hidden="true"></span><span class="vh"></span>`;
  // each hover turns the star on by one point
  let deg = 0;
  link.addEventListener("pointerenter", () =>
    box.style.setProperty("--r", `${(deg += TURN)}deg`),
  );

  const saved = load();
  if (saved && COUNT.test(saved)) show(link, box, saved, false);
  void fetchCount(new URL(link.href).pathname).then((count) => {
    if (!count) return;
    save(count);
    show(link, box, count, true);
  });
}
