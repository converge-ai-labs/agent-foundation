import { CALLS, EARLIER, ENDS, type ThreadState } from "../content";
import { $, $$, context2d } from "../dom";
import { turn } from "../mark";
import {
  clamp,
  EASE,
  easeInOut,
  leg,
  type Point,
  rand,
  reduced,
} from "../motion";
import { roll } from "../roll";
import { BLANK, type Char, charsOf } from "./code";
import { LIGHTS } from "./lights";

const EVERY = 2600; // ms between calls
const BOARD = 4; // threads on the board
const FLIGHT = 520; // ms from the code to the Service
const TRAIL = 18; // segments in a light's streak

const TURN_AWAY: Keyframe[] = [
  { transform: "perspective(400px)" },
  { transform: "perspective(400px) rotateY(90deg)" },
];
const TURN_IN: Keyframe[] = [
  { transform: "perspective(400px) rotateY(-90deg)" },
  { transform: "perspective(400px)" },
];

// every call becomes a grid of cells, so a switch turns only the cells
// that differ
const VARIANTS = CALLS.map((c) => c.tasks.map((t) => charsOf(c.code(t))));
const ROWS = Math.max(...VARIANTS.flat().map((lines) => lines.length));
const COLS = Math.max(...VARIANTS.flat(2).map((line) => line.length));

/** A character cell: the glyph it shows and, while it turns, the one leaving under it. */
interface Cell extends Char {
  el: HTMLSpanElement;
  glyph: HTMLSpanElement;
}

interface Thread {
  li: HTMLLIElement;
  state: HTMLElement;
  is: ThreadState;
  at: number; // when it moves on
}

interface Flight {
  task: string;
  agent: string;
  t0: number;
  from: Point;
  to: Point;
}

const toneClass = (c: Char) =>
  [c.tone && `t-${c.tone}`, c.key && "k"].filter(Boolean).join(" ");

function glyph(c: Char) {
  const el = document.createElement("span");
  el.textContent = c.ch;
  el.className = toneClass(c);
  return el;
}

/** Act two: the same call from every SDK, or plain HTTP, and the threads it starts on the Service. */
export class Call {
  readonly layer = $("#call");
  readonly title = $("#call-title");
  readonly node = $("#service-node");
  readonly deploy = $("#call .deploys-sub");
  // they come in one after another
  readonly parts = [
    $("#langs"),
    $("#code"),
    $("#calls .board-title"),
    $("#threads"),
  ];
  on = false; // drawn
  active = false; // calling

  private readonly langs = $$("#langs button");
  private readonly code = $("#code");
  private readonly threads = $("#threads");
  private readonly canvas = $<HTMLCanvasElement>("#calls-canvas");
  private readonly ctx = context2d(this.canvas);
  private readonly mark = $(".mk", this.node);
  private readonly cells: Cell[][];
  private readonly rounds = CALLS.map(() => 0);
  private index = 0;
  private shown = { task: "", lines: [] as Char[][] };
  private next = 0;
  private fireAt = Infinity;
  private live: Thread[] = [];
  private flights: Flight[] = [];

  constructor() {
    this.cells = Array.from({ length: ROWS }, () =>
      Array.from({ length: COLS }, () => {
        const el = document.createElement("span");
        el.className = "c";
        const cell = { ...BLANK, el, glyph: glyph(BLANK) };
        el.append(cell.glyph);
        return cell;
      }),
    );
    this.code.replaceChildren(
      ...this.cells.flatMap((row, r) => [
        ...(r ? ["\n"] : []),
        ...row.map((c) => c.el),
      ]),
    );
    this.show(0, true);
    for (const [task, agent, state] of EARLIER)
      this.addThread(task, agent, state, true);
    // the rail follows CALLS: pick a language and its call goes out
    this.langs.forEach((b, k) =>
      b.addEventListener("click", () => {
        const now = performance.now();
        this.show(k);
        this.fireAt = now + 900;
        this.next = now + EVERY;
      }),
    );
    this.size();
  }

  /** Fits the light's canvas to the act; call on resize. */
  size() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(this.canvas.clientWidth * dpr);
    this.canvas.height = Math.round(this.canvas.clientHeight * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  tick(now: number) {
    if (!this.on || reduced) return;
    this.drawFlights(now);
    if (!this.active) return;
    if (now >= this.next) {
      if (this.next) this.show((this.index + 1) % CALLS.length);
      // the first look already has a thread finishing
      else for (const t of this.live) if (t.is === "running") t.at = now + 1400;
      this.fireAt = now + (this.next ? 900 : 400);
      this.next = now + EVERY;
    }
    if (now >= this.fireAt) {
      this.fireAt = Infinity;
      this.fire(now);
    }
    // accepted, then running, then done or waiting for an answer
    for (const t of this.live) {
      if (now < t.at) continue;
      if (t.is === "accepted") {
        this.setState(t, "running", 0);
        t.at = now + rand(2200, 4200);
      } else {
        this.setState(t, ENDS[Math.floor(Math.random() * ENDS.length)], 0);
        t.at = Infinity;
      }
    }
  }

  // each cell turns edge-on and back, like the pleats of the name, in a
  // wave from the top left
  private show(i: number, first = false) {
    const v = this.rounds[i]++ % 2;
    this.index = i;
    this.shown = { task: CALLS[i].tasks[v], lines: VARIANTS[i][v] };
    this.langs.forEach((b, k) =>
      b.setAttribute("aria-pressed", String(k === i)),
    );
    this.cells.forEach((row, r) =>
      row.forEach((cell, c) => {
        const to = this.shown.lines[r]?.[c] ?? BLANK;
        if (cell.ch === to.ch && cell.key === to.key && cell.tone === to.tone)
          return;
        const same = cell.ch === to.ch;
        Object.assign(cell, to);
        // the same character only changes color
        if (same) cell.glyph.className = toneClass(cell);
        else if (first || reduced) {
          cell.glyph = glyph(cell);
          cell.el.replaceChildren(cell.glyph);
        } else this.flip(cell, c * 9 + r * 60);
      }),
    );
  }

  // the old glyph turns away and the new one, laid over it, turns in; both
  // are scheduled at once, so a busy page never strands a cell edge-on
  private flip(cell: Cell, delay: number) {
    // a turn still under way gives way to this one
    for (const el of [...cell.el.children]) if (el !== cell.glyph) el.remove();
    const old = cell.glyph;
    cell.glyph = glyph(cell);
    cell.el.append(cell.glyph);
    old.animate(TURN_AWAY, {
      duration: 140,
      delay,
      easing: "cubic-bezier(0.5, 0, 0.75, 0)",
      fill: "forwards",
    }).onfinish = () => old.remove();
    cell.glyph.animate(TURN_IN, {
      duration: 320,
      delay: delay + 140,
      easing: EASE,
      fill: "backwards",
    });
  }

  private setState(t: Thread, state: ThreadState, delay?: number) {
    t.is = state;
    t.state.dataset.s = state;
    roll(t.state, `<span class="it"><i></i>${state}</span>`, delay);
  }

  // a new thread folds in at the top and the board steps down one row
  private addThread(
    task: string,
    agent: string,
    state: ThreadState,
    first = false,
  ) {
    const li = document.createElement("li");
    li.className = "thread";
    li.innerHTML = `<b>${task}</b><i>${agent}</i><span class="state slot"></span>`;
    const t: Thread = { li, state: $(".state", li), is: state, at: Infinity };
    this.setState(t, state);
    this.threads.prepend(li);
    this.live.push(t);
    const rows = [...this.threads.children];
    if (!first && !reduced) {
      li.animate(
        [
          { transform: "rotateX(-90deg)", opacity: 0 },
          { transform: "none", opacity: 1 },
        ],
        {
          duration: 700,
          easing: EASE,
        },
      );
      for (const row of rows.slice(1))
        row.animate(
          [
            { transform: `translateY(${-li.offsetHeight}px)` },
            { transform: "none" },
          ],
          {
            duration: 620,
            easing: EASE,
          },
        );
    }
    // the oldest thread steps off the bottom of the board
    for (const row of rows.slice(BOARD)) setTimeout(() => row.remove(), 700);
    this.live = this.live.filter((x) => rows.indexOf(x.li) < BOARD);
    return t;
  }

  // the call goes out: what it asks for lights up, and a light leaves the
  // end of its line for the Service
  private fire(now: number) {
    this.code.classList.add("fire");
    setTimeout(() => this.code.classList.remove("fire"), 900);
    // it rises clear of every line of code and of the rail above them
    const lines = this.shown.lines;
    const r = lines.findIndex((line) => line.some((c) => c.key));
    const ends = lines.map((line, k) => this.cells[k][line.length - 1].el);
    const rail = this.langs[this.langs.length - 1];
    const x0 =
      Math.max(
        rail.offsetLeft + rail.offsetWidth,
        ...ends.map((el) => el.offsetLeft + el.offsetWidth),
      ) + 18;
    const n = this.node;
    this.flights.push({
      task: this.shown.task,
      agent: CALLS[this.index].agent,
      t0: now,
      from: [x0, ends[r].offsetTop + ends[r].offsetHeight / 2],
      to: [n.offsetLeft + n.offsetWidth / 2, n.offsetTop + n.offsetHeight / 2],
    });
  }

  // it lands: the mark turns once and the call is a thread on the board
  private land(f: Flight, now: number) {
    this.addThread(f.task, f.agent, "accepted").at = now + rand(380, 560);
    turn(this.mark);
  }

  // the light rises from the code, then runs level into the Service, a
  // streak that thins out behind it
  private drawFlights(now: number) {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.canvas.clientWidth, this.canvas.clientHeight);
    ctx.globalCompositeOperation = "lighter";
    ctx.lineCap = "round";
    ctx.strokeStyle = "rgb(190,186,255)";
    this.flights = this.flights.filter((f) => {
      const u = (now - f.t0) / FLIGHT;
      if (u >= 1) {
        this.land(f, now);
        return false;
      }
      const at = (v: number) => leg(f.from, f.to, easeInOut(clamp(v, 0, 1)));
      for (let k = 0; k < TRAIL; k++) {
        const [xa, ya] = at(u - (k + 1) * 0.016);
        const [xb, yb] = at(u - k * 0.016);
        ctx.globalAlpha = 0.9 * (1 - k / TRAIL) ** 2;
        ctx.lineWidth = 2.4 * (1 - k / TRAIL);
        ctx.beginPath();
        ctx.moveTo(xa, ya);
        ctx.lineTo(xb, yb);
        ctx.stroke();
      }
      const [x, y] = at(u);
      ctx.globalAlpha = 1;
      ctx.drawImage(LIGHTS.run, x - 16, y - 16, 32, 32);
      return true;
    });
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
  }
}
