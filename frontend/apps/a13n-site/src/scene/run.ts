import { $, context2d } from "../dom";
import { clamp, easeInOut, type Point, rand, reduced, smooth } from "../motion";
import { LIGHTS } from "./lights";

const HINT = matchMedia("(hover: none)").matches
  ? "Tap a worker to take it offline."
  : "Click a worker to take it offline.";
const COLUMNS = 6; // session columns per worker
const BUSY = 0.62; // share of slots the fleet keeps busy
const DOWN_FOR = 3800; // ms a worker stays offline before it is replaced
const CLEAR = 104; // px of the stage the wall leaves to the nav bar and the air below
const fmt = (n: number) => n.toLocaleString("en-US");

interface Rack {
  i: number;
  id: string;
  x: number;
  slots: (Session | null)[];
  n: number; // sessions seated
  down: number; // when it went offline, or 0
  back: number; // when it was replaced, or 0
}

interface Session {
  rack: Rack;
  slot: number;
  x: number;
  y: number;
  born: number;
  life: number;
  phase: number; // offsets its breath and checkpoints from its neighbors'
  state: "run" | "wait" | "fly" | "end";
  moved: number; // when it last landed on another worker
  waitAt?: number; // when it will stop for input
  waitFor?: number;
  until?: number; // when the input comes
  endAt?: number;
  fly?: {
    x0: number;
    y0: number;
    t0: number;
    dur: number;
    was: "run" | "wait";
  };
}

/** Act three: a wall of sessions across a fleet of workers, any of which can go offline. */
export class Run {
  readonly layer = $("#run");
  readonly title = $("#run-title");
  readonly canvas = $<HTMLCanvasElement>("#fleet-canvas");
  readonly parts = [$(".fleet-stats"), $(".fleet-foot")];
  on = false; // drawn
  reveal = 0; // how far the wall is lit, 0 to 1
  /** Where the wall starts to light, in px from the canvas. */
  seed: Point = [0, 0];

  private readonly ctx = context2d(this.canvas);
  private readonly mono: string;
  private readonly say = $("#fleet-say");
  private readonly stats = {
    running: $("#st-running"),
    workers: $("#st-workers"),
    done: $("#st-done"),
  };
  // the wall, laid out for the viewport
  private W = 0;
  private H = 0;
  private p = 0; // slot pitch
  private rows = 0;
  private top = 0;
  private far = 0;
  private racks: Rack[] = [];
  private readonly bg = document.createElement("canvas"); // one faint dot per slot
  private sessions: Session[] = [];
  private t = 0; // fleet time, in ms
  private last = 0;
  private stamp = 0; // when the stats were last written
  private done = 0;
  private nextFail = 0;
  private hover: Rack | null = null;
  private msg: { text: string; tone: string; until: number } | null = null;

  /** mono: the font stack for the worker names. */
  constructor(mono: string) {
    this.mono = mono;
    this.layout();
    this.canvas.addEventListener("pointermove", (e) => {
      this.hover = this.rackAt(e);
      this.canvas.style.cursor =
        this.hover && !this.hover.down ? "pointer" : "";
    });
    this.canvas.addEventListener("pointerleave", () => (this.hover = null));
    // any worker can be taken offline by hand, while half the fleet is up
    this.canvas.addEventListener("click", (e) => {
      const rack = this.rackAt(e);
      if (!rack || rack.down || this.up().length <= this.racks.length / 2)
        return;
      this.fail(rack);
      this.nextFail = this.t + 6000;
      this.stamp = 0;
    });
  }

  /** Builds the wall for the room the stage has left, busy from the start; call on resize. */
  layout() {
    const W = this.canvas.clientWidth;
    const narrow = W < 640;
    const R = narrow ? 6 : 16;
    // two empty columns between racks
    const p = W / (R * COLUMNS + (R - 1) * 2);
    const chrome =
      $(".wrap", this.layer).offsetHeight - this.canvas.offsetHeight;
    const room = $("#stage").clientHeight - CLEAR - chrome;
    const rows = clamp(Math.floor((room - 30) / p) - 1, 10, narrow ? 20 : 24);
    // room above for glow and flight arcs, below for the worker names
    const top = Math.round(p);
    const H = Math.round(top + rows * p + 30);
    const dpr = Math.min(devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(W * dpr);
    this.canvas.height = Math.round(H * dpr);
    this.canvas.style.height = `${H}px`;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.W = W;
    this.H = H;
    this.p = p;
    this.rows = rows;
    this.top = top;
    this.sessions = [];
    this.hover = null;
    this.msg = null;
    this.nextFail = 0;
    this.racks = Array.from({ length: R }, (_, i) => ({
      i,
      id: `w${String(i + 1).padStart(2, "0")}`,
      x: i * (COLUMNS + 2) * p,
      slots: new Array<Session | null>(COLUMNS * rows).fill(null),
      n: 0,
      down: 0,
      back: 0,
    }));
    this.seed = [W / 2, top + (rows * p) / 2];
    this.far = Math.hypot(W / 2, H / 2) + 40;
    this.bg.width = this.canvas.width;
    this.bg.height = this.canvas.height;
    const b = context2d(this.bg);
    b.setTransform(dpr, 0, 0, dpr, 0, 0);
    b.fillStyle = "rgba(255,255,255,0.07)";
    for (const r of this.racks)
      for (let k = 0; k < COLUMNS * rows; k++) {
        b.beginPath();
        b.arc(...this.slotXY(r, k), 1, 0, Math.PI * 2);
        b.fill();
      }
    // the fleet was busy before you arrived
    while (this.sessions.length < this.target()) this.spawn(true);
    this.draw();
  }

  tick(now: number) {
    const dt = Math.min(now - (this.last || now), 50);
    this.last = now;
    if (!this.on) return;
    if (!reduced) this.step(dt);
    this.draw();
    if (now - this.stamp > 200) {
      this.stamp = now;
      this.report();
    }
  }

  private up() {
    return this.racks.filter((r) => !r.down);
  }

  private target() {
    return Math.round(BUSY * this.up().length * COLUMNS * this.rows);
  }

  private slotXY(rack: Rack, k: number): Point {
    return [
      rack.x + ((k % COLUMNS) + 0.5) * this.p,
      this.top + (Math.floor(k / COLUMNS) + 0.5) * this.p,
    ];
  }

  private seat(s: Session, rack: Rack) {
    let slot = -1;
    for (let tries = 0; tries < 24 && slot < 0; tries++) {
      const k = Math.floor(Math.random() * rack.slots.length);
      if (!rack.slots[k]) slot = k;
    }
    if (slot < 0) slot = rack.slots.indexOf(null);
    rack.slots[slot] = s;
    rack.n++;
    s.rack = rack;
    s.slot = slot;
    [s.x, s.y] = this.slotXY(rack, slot);
  }

  // two random workers, and the lighter one takes the session
  private spawn(old: boolean) {
    const open = this.racks.filter((r) => !r.down && r.n < r.slots.length);
    if (!open.length) return;
    const one = open[Math.floor(Math.random() * open.length)];
    const two = open[Math.floor(Math.random() * open.length)];
    const rack = one.n <= two.n ? one : two;
    const life = rand(16000, 56000);
    const born = this.t - (old ? Math.random() * life : 0);
    const s: Session = {
      rack,
      slot: 0,
      x: 0,
      y: 0,
      born,
      life,
      phase: rand(0, 7),
      state: "run",
      moved: -1e9,
    };
    if (Math.random() < 0.16) {
      s.waitAt = born + rand(0.2, 0.7) * life;
      s.waitFor = rand(3000, 9000);
    }
    this.seat(s, rack);
    this.sessions.push(s);
  }

  // each session resumes from its last checkpoint on a nearby worker
  private fail(rack: Rack) {
    const t = this.t;
    rack.down = t;
    rack.back = 0;
    const moving = rack.slots
      .filter((s): s is Session => s !== null)
      .sort(() => Math.random() - 0.5);
    rack.slots.fill(null);
    rack.n = 0;
    const up = this.up().sort(
      (a, b) => Math.abs(a.i - rack.i) - Math.abs(b.i - rack.i),
    );
    moving.forEach((s, k) => {
      const near = up.slice(0, 5).filter((r) => r.n < r.slots.length);
      const to = near.length
        ? near[Math.floor(Math.random() * near.length)]
        : up.find((r) => r.n < r.slots.length);
      if (!to) return; // never: the fleet keeps over a third of every worker free
      const [x0, y0] = [s.x, s.y];
      this.seat(s, to);
      const t0 = t + 380 + (k / moving.length) * 900;
      s.fly = {
        x0,
        y0,
        t0,
        dur: rand(760, 1100),
        was: s.state === "wait" ? "wait" : "run",
      };
      s.state = "fly";
      s.moved = t0 + s.fly.dur;
    });
    this.msg = {
      text: `${rack.id} offline · ${moving.length} sessions moved`,
      tone: "down",
      until: t + 3900,
    };
  }

  private step(dt: number) {
    const t = (this.t += dt);
    // the first failure comes just after the wall is fully lit, then often
    if (this.reveal < 1) this.nextFail = Math.max(this.nextFail, t + 900);
    if (t >= this.nextFail) {
      const up = this.up();
      if (up.length > this.racks.length / 2)
        this.fail(up[Math.floor(Math.random() * up.length)]);
      this.nextFail = t + rand(4200, 5600);
    }
    for (const r of this.racks)
      if (r.down && t - r.down > DOWN_FOR) {
        r.down = 0;
        r.back = t;
        this.msg = { text: `${r.id} replaced`, tone: "up", until: t + 2400 };
      }
    const live = this.sessions;
    for (let i = live.length - 1; i >= 0; i--) {
      const s = live[i];
      if (s.state === "fly" && s.fly && t >= s.fly.t0 + s.fly.dur)
        s.state = s.fly.was;
      else if (s.state === "run" && s.waitAt && t >= s.waitAt) {
        s.state = "wait";
        s.until = t + (s.waitFor ?? 0);
        s.waitAt = 0;
      } else if (s.state === "wait" && t >= (s.until ?? 0)) s.state = "run";
      else if (s.state === "run" && t - s.born >= s.life) {
        s.state = "end";
        s.endAt = t;
        s.rack.slots[s.slot] = null;
        s.rack.n--;
        this.done++;
      } else if (s.state === "end" && t - (s.endAt ?? 0) > 700)
        live.splice(i, 1);
    }
    let missing = this.target() - live.filter((s) => s.state !== "end").length;
    for (let k = 0; k < 6 && missing > 0; k++, missing--) this.spawn(false);
  }

  private report() {
    const up = this.up().length;
    this.stats.running.textContent = fmt(
      this.sessions.filter((s) => s.state !== "end").length,
    );
    this.stats.workers.textContent = `${up}/${this.racks.length}`;
    this.stats.done.textContent = fmt(this.done);
    const msg = this.msg && this.t < this.msg.until ? this.msg : null;
    const h = this.hover;
    const text = msg
      ? msg.text
      : h && !h.down
        ? `${h.id} · ${fmt(h.n)} sessions`
        : HINT;
    if (this.say.textContent !== text) this.say.textContent = text;
    this.say.dataset.tone = msg ? msg.tone : h ? "on" : "";
  }

  private rackAt(e: MouseEvent) {
    if (this.reveal < 1) return null;
    const r = this.canvas.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    const { p } = this;
    const rack = this.racks[Math.floor((x + p) / ((COLUMNS + 2) * p))];
    return rack &&
      x > rack.x - p &&
      x < rack.x + (COLUMNS + 1) * p &&
      y >= 0 &&
      y <= this.H
      ? rack
      : null;
  }

  private outline(r: Rack, color: string, dash = false) {
    const { ctx, p, rows, top } = this;
    ctx.strokeStyle = color;
    ctx.setLineDash(dash ? [2, 4] : []);
    ctx.beginPath();
    ctx.roundRect(
      r.x - p * 0.35 + 0.5,
      top + 0.5,
      COLUMNS * p + p * 0.7 - 1,
      rows * p - 1,
      4,
    );
    ctx.stroke();
    ctx.setLineDash([]);
  }

  private draw() {
    const { ctx, W, H, p, rows, top, seed, t } = this;
    ctx.clearRect(0, 0, W, H);
    if (this.reveal <= 0) return;
    // the wall lights up in a ring from where the agent landed
    const R = this.reveal >= 1 ? Infinity : this.reveal * this.far;
    ctx.save();
    if (R < Infinity) {
      ctx.beginPath();
      ctx.arc(seed[0], seed[1], R, 0, Math.PI * 2);
      ctx.clip();
    }
    ctx.drawImage(this.bg, 0, 0, W, H);
    ctx.restore();

    ctx.lineWidth = 1;
    ctx.globalAlpha = smooth(0.8, 1, this.reveal);
    ctx.font = `500 10.5px ${this.mono}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    for (const r of this.racks) {
      const since = t - r.back;
      let label = "rgba(255,255,255,0.32)";
      if (r.down) {
        ctx.fillStyle = "rgba(255,92,97,0.05)";
        ctx.fillRect(r.x - p * 0.35, top, COLUMNS * p + p * 0.7, rows * p);
        this.outline(r, "rgba(255,92,97,0.55)", true);
        label = "rgb(255,125,129)";
      } else if (r.back && since < 1600) {
        const a = 1 - since / 1600;
        this.outline(r, `rgba(58,210,159,${(0.7 * a).toFixed(3)})`);
        label = `rgba(58,210,159,${Math.max(a, 0.32).toFixed(3)})`;
      } else if (r === this.hover) {
        this.outline(r, "rgba(255,255,255,0.2)");
        label = "rgba(255,255,255,0.9)";
      }
      ctx.fillStyle = label;
      ctx.fillText(r.id, r.x + (COLUMNS * p) / 2, top + rows * p + 22);
    }

    ctx.globalCompositeOperation = "lighter";
    const D = p * 2.2;
    const dot = (
      img: HTMLCanvasElement,
      x: number,
      y: number,
      a: number,
      size = D,
    ) => {
      if (a <= 0) return;
      ctx.globalAlpha = Math.min(a, 1);
      ctx.drawImage(img, x - size / 2, y - size / 2, size, size);
    };
    // sessions near the front of the ring flare as it passes
    const flare = (s: Session) => {
      if (R === Infinity) return 1;
      const d = Math.hypot(s.x - seed[0], s.y - seed[1]);
      return d > R ? 0 : 1 + 1.4 * Math.max(0, 1 - (R - d) / 40);
    };
    for (const s of this.sessions) {
      const f = flare(s);
      if (!f) continue;
      if (s.state === "fly" && s.fly) {
        const fl = s.fly;
        if (t < fl.t0) {
          // stranded for a beat: the worker is gone, the session is not
          dot(
            LIGHTS.moved,
            fl.x0,
            fl.y0,
            0.55 + 0.45 * Math.sin((t - fl.t0) / 70) ** 2,
          );
          continue;
        }
        const u = easeInOut(Math.min((t - fl.t0) / fl.dur, 1));
        const rise = 24 + Math.abs(s.x - fl.x0) * 0.22;
        const cx = (fl.x0 + s.x) / 2;
        const cy = Math.max(Math.min(fl.y0, s.y) - rise, -top);
        const at = (v: number): Point => {
          const q = 1 - v;
          return [
            q * q * fl.x0 + 2 * q * v * cx + v * v * s.x,
            q * q * fl.y0 + 2 * q * v * cy + v * v * s.y,
          ];
        };
        for (let k = 3; k >= 1; k--)
          dot(
            LIGHTS.moved,
            ...at(Math.max(u - k * 0.035, 0)),
            0.22 / k,
            D * 0.9,
          );
        dot(LIGHTS.moved, ...at(u), 1, D * 1.25);
        continue;
      }
      if (s.state === "end") {
        const k = (t - (s.endAt ?? t)) / 700;
        dot(LIGHTS.run, s.x, s.y, 1 - k, D * (1 + k * 0.8));
        continue;
      }
      const fade = Math.min((t - s.born) / 500, 1);
      if (s.state === "wait") {
        dot(LIGHTS.wait, s.x, s.y, 0.55 * fade * f, D * 0.42);
        continue;
      }
      // working: a slow breath, and a brief blip at each checkpoint
      const beat = ((t + s.phase * 900) % 4800) / 4800;
      const a =
        (0.62 + 0.28 * Math.sin(t / 900 + s.phase) + (beat < 0.04 ? 0.6 : 0)) *
        fade *
        f;
      const red = Math.max(0, 1 - (t - s.moved) / 2600);
      if (red > 0) dot(LIGHTS.moved, s.x, s.y, a * red);
      dot(LIGHTS.run, s.x, s.y, a * (1 - red));
    }
    if (R < Infinity) {
      ctx.globalAlpha = 0.5 * (1 - this.reveal);
      ctx.strokeStyle = "rgb(150,142,255)";
      ctx.beginPath();
      ctx.arc(seed[0], seed[1], R, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
  }
}
