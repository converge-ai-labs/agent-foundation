import { brandIcon } from "./brands";
import { STACK } from "./content";
import { $, $$, cssVar, fontsReady } from "./dom";
import { Build } from "./scene/build";
import { Call } from "./scene/call";
import { Run } from "./scene/run";
import { Stage } from "./scene/stage";

const COPIED_FOR = 1600; // ms

/** The providers, a row of brand icons per kind; names show on hover. */
function renderStack() {
  $("#stack").innerHTML = STACK.map(
    ({ title, brands, more }) =>
      `<div class="row" data-in><h3>${title}</h3><ul class="logos">` +
      brands
        .map(
          (name, i) =>
            `<li class="logo" style="--i:${i}" data-name="${name}"><img src="${brandIcon(name)}" alt="${name}" loading="lazy" decoding="async" referrerpolicy="no-referrer" /></li>`,
        )
        .join("") +
      (more
        ? `<li class="more" style="--i:${brands.length}">${more}</li>`
        : "") +
      `</ul></div>`,
  ).join("");
}

/** Brand icons named in the markup with data-brand. */
function mountBrandIcons() {
  for (const img of $$<HTMLImageElement>("img[data-brand]"))
    img.src = brandIcon(img.dataset.brand ?? "");
}

/** Statements rise word by word; their gray continuation keeps its color. */
function splitWords() {
  for (const el of $$("[data-words]")) {
    let i = 0;
    const wrap = (text: string, dim: boolean) =>
      text
        .split(/(\s+)/)
        .map((w) =>
          w.trim()
            ? `<span class="w${dim ? " dim" : ""}" style="--i:${i++}">${w}</span>`
            : w,
        )
        .join("");
    el.innerHTML = [...el.childNodes]
      .map((n) => wrap(n.textContent ?? "", n.nodeType !== Node.TEXT_NODE))
      .join("");
  }
}

/**
 * Reveals come in as they scroll into view, in order within each section.
 * Statements marked data-scroll follow the scene instead.
 */
function observeReveals() {
  for (const section of $$("main > section"))
    $$("[data-reveal]", section).forEach((el, k) =>
      el.style.setProperty("--d", `${120 + k * 90}ms`),
    );
  const io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        e.target.classList.add("in");
        io.unobserve(e.target);
      }
    },
    { rootMargin: "0px 0px -10% 0px" },
  );
  for (const el of $$(
    "[data-reveal], [data-words]:not([data-scroll]), [data-in]",
  ))
    io.observe(el);
}

function bindCopyButtons() {
  for (const button of $$<HTMLButtonElement>(".copy")) {
    const say = $(".copy-say", button);
    let timer = 0;
    say.textContent = "Copy";
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy ?? "");
      } catch {
        return;
      }
      say.textContent = "Copied";
      button.classList.add("done");
      clearTimeout(timer);
      timer = window.setTimeout(() => {
        say.textContent = "Copy";
        button.classList.remove("done");
      }, COPIED_FOR);
    });
  }
}

/**
 * Everything below the hero: the Service scene, the providers, and the end.
 * It loads after the hero is on screen.
 */
export class Page {
  private constructor(
    private readonly stage: Stage,
    private readonly call: Call,
    private readonly run: Run,
  ) {}

  static async mount() {
    const mono = cssVar("--mono");
    // the canvases measure and draw with the text and code faces
    await fontsReady(`400 16px ${cssVar("--text")}`, `400 16px ${mono}`);
    mountBrandIcons();
    renderStack();
    splitWords();
    bindCopyButtons();
    const build = new Build();
    const run = new Run(mono);
    const call = new Call();
    const stage = new Stage(build, call, run);
    observeReveals();
    return new Page(stage, call, run);
  }

  /** Whether the scene has turned the page to night. */
  get dark() {
    return this.stage.dark;
  }

  layout() {
    this.run.layout();
    this.call.size();
    this.stage.layout();
  }

  tick(now: number, dt: number) {
    this.stage.tick(now, dt);
    this.call.tick(now);
    this.run.tick(now);
  }
}
