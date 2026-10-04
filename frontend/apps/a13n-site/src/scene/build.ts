import { brandIcon } from "../brands";
import { AGENTS, type AgentSpec } from "../content";
import { $, $$ } from "../dom";
import { reduced } from "../motion";
import { roll } from "../roll";

const EVERY = 2400; // ms each agent stays on the sheet

const words = (text: string) =>
  text
    .split(" ")
    .map((w) => `<span class="it">${w}</span>`)
    .join("");
// a space alone in a flex item would collapse away, so keep it hard
const chars = (text: string) =>
  [...text]
    .map((c) => `<span class="it">${c === " " ? "&nbsp;" : c}</span>`)
    .join("");
const icon = (name: string) =>
  `<img class="ic" src="${brandIcon(name)}" alt="" referrerpolicy="no-referrer" />`;

/** How each row of the sheet shows its part of an agent, by data-field. */
const FIELDS: Record<string, (a: AgentSpec) => string> = {
  name: (a) => words(a.name),
  instructions: (a) => words(a.instructions),
  model: (a) =>
    `<span class="it">${icon(a.model.brand)}</span><span class="mono">${chars(a.model.id)}</span>`,
  skills: (a) =>
    a.skills.map((s) => `<span class="it mono">${s}</span>`).join(""),
  connections: (a) =>
    a.connections.map((c) => `<span class="it">${icon(c)}${c}</span>`).join(""),
  sandbox: (a) => `<span class="it">${icon(a.sandbox)}${a.sandbox}</span>`,
};

/** Act one: a sheet describes an agent; every part of it can change. */
export class Build {
  readonly layer = $("#build");
  readonly title = $("#build .statement");
  readonly sheet = $("#agent");
  readonly name = $(".agent-name dd", this.sheet);
  // rows under the name, from the bottom up, as they fold away
  readonly rows = $$(".agent-row", this.sheet).slice(1).reverse();
  private readonly slots = $$(".slot", this.sheet).map((slot) => ({
    slot,
    render: FIELDS[slot.dataset.field ?? ""],
  }));
  private shown = 0;
  private next = 0;

  constructor() {
    // warm every icon, so a swap never waits on the network
    for (const a of AGENTS)
      for (const name of [a.model.brand, a.sandbox, ...a.connections])
        new Image().src = brandIcon(name);
    this.show(0, true);
    this.sheet.addEventListener("click", () => {
      this.show((this.shown + 1) % AGENTS.length);
      this.next = performance.now() + EVERY;
    });
  }

  /** One agent after another, on a loop, while the sheet is in view. */
  tick(now: number, inView: boolean) {
    if (reduced || !inView || !this.sheet.classList.contains("in")) return;
    if (now < this.next) return;
    if (this.next) this.show((this.shown + 1) % AGENTS.length);
    this.next = now + (this.next ? EVERY : 1600);
  }

  private show(i: number, first = false) {
    this.shown = i;
    this.slots.forEach(({ slot, render }, k) =>
      roll(slot, render(AGENTS[i]), first ? undefined : k * 60),
    );
  }
}
