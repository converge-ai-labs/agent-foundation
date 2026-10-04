import { $, context2d } from "./dom";
import { createMark } from "./mark";
import { clamp, DEG, reduced, smooth, Spring } from "./motion";

/*
 * The name is one line of pleats with the mark on it. Closed, the mark leads
 * "a13n" as in the nav lockup. Opening, a13n folds edge-on into the mark,
 * then "agent foundation" unfolds behind it and pushes it to the far end;
 * closing runs the other way. The mark crosses a glyph only while that glyph
 * is edge-on, so nothing ever overlaps. On a narrow measure the open name
 * takes two lines: the first ("agent" and the mark) rises, and "foundation"
 * unfolds on the baseline below it.
 */

const SHORT = "a13n";
const FULL = "agent foundation";
const TRACK = -0.065; // the brand's tracking, in ems
// set for display size: the mark's ink runs from the baseline to the cap
// line, and stands GAP em from the word, ink to ink
const INK_H = 0.866; // the mark's ink height, in mark boxes
const INK_W = 0.454; // from the mark's center to its ink edge, in mark boxes
const GAP = 0.24;
const EDGE = 0.02; // a glyph turned this far has no width left to cross
// closed, the name stands this much larger than open; more on a phone,
// where the open line is small
const ZOOM = 1.7;
const ZOOM_NARROW = 2.4;
const GROUND = 0.26; // from the baseline down to the slogan, in ems
const LEAD = 1.02; // from the first open line down to the second, in ems
const NARROW = 640; // a measure below this, in px, opens the name on two lines
const SCROLLED = 8; // px down the page that opens the name
// a pointer this near the name, in px, opens it; this far closes it
const OPEN_NEAR = 8;
const CLOSE_FAR = 20;
const LEAN_FAR = 260; // the closed 1 and 3 start to lean open this near

type RGB = [number, number, number];
const INK: RGB = [11, 11, 12];
const GRAY: RGB = [168, 168, 176];
const PAPER: RGB = [251, 251, 253];
const mix = (p: RGB, q: RGB, t: number) =>
  p.map((v, i) => v + (q[i] - v) * t) as RGB;
const rgb = (c: RGB) => `rgb(${c.map((v) => Math.round(v)).join(",")})`;

/** "a" and "n" end both forms, "1" and "3" only the short one, the rest only the full one. */
type Kind = "end" | "digit" | "mid";

interface Glyph {
  el: HTMLSpanElement;
  adv: number; // advance, in ems
  kind: Kind;
  fold: Spring; // degrees turned away from us
  sign: number; // neighbors turn opposite ways, like pleats
  ox: number; // its vertical center line, in px from its origin
}

/** A line in ems: each glyph's origin, the mark's center, and the ink edges. */
interface Line {
  xs: number[];
  at: number;
  l: number;
  r: number;
}

/** Display face metrics, in ems. */
interface Metrics {
  cap: number; // the cap height
  base: number; // the baseline, below the top of a line-height: 1 box
  inkA: number; // the a's ink edge, from its origin
  inkN: number; // the n's ink edge, from its origin
  box: number; // the mark's box
  markW: number; // the mark's ink width
  // the mark's gaps, from its ink to the next glyph's box
  padL: number;
  padR: number;
}

export class Hero {
  private readonly section = $(".hero");
  private readonly name = $("#name");
  private readonly pitch = $(".hero .pitch");
  private readonly em: Metrics;
  private readonly glyphs: Glyph[];
  private readonly mark = createMark(100);
  private readonly shadowLayer: SVGSVGElement;
  private readonly faceLayer: SVGSVGElement;
  // the resting lines: closed, open, and the first of two open lines
  private readonly shut: Line;
  private readonly full: Line;
  private readonly first: Line;
  private readonly space: number; // where two open lines break
  private readonly second: number; // the second open line's width, in ems

  // laid out for the viewport, in px from the name's box
  private left = 0; // where the measure starts
  private room = 0; // the measure
  private two = false; // whether the open name takes two lines
  private size = 0; // the closed name's font size
  private ground = 0;
  private height = 0;
  private zone = { l: 0, r: 0, t: 0, b: 0 }; // where the pointer opens it

  private open = false;
  private settled = true; // closed and done with every fold
  private home = true; // closed and a13n unfolding again
  private touched = false; // the visitor opened or closed it
  private scrolled = false;
  private dirty = true;
  private near = 0; // how near the pointer is, 0 to 1
  private pointer: PointerEvent | null = null;
  private cursor = 0; // the mark stands before this glyph
  private turn = 0; // the mark's turn, in degrees
  private readonly lift = new Spring(0, 160, 25); // the first of two lines rising
  private readonly shadow = new Spring(0, 120, 14); // the shadow layer follows the face
  private queue: { at: number; run: () => void }[] = [];
  // glyphs that unfold one after another, each once it is on its side of
  // the mark and step ms after the one before
  private wave: { glyphs: Glyph[]; step: number; next: number } | null = null;

  constructor(display: string) {
    const ctx = context2d(document.createElement("canvas"));
    ctx.font = `700 100px ${display}`;
    ctx.fontKerning = "none";
    const advances = (text: string) =>
      [...text].map((c) => ctx.measureText(c).width / 100 + TRACK);
    const A = advances(SHORT);
    const B = advances(FULL);
    const one = ctx.measureText("1");
    const cap = one.actualBoundingBoxAscent / 100;
    const box = cap / INK_H;
    const inkA = -ctx.measureText("a").actualBoundingBoxLeft / 100;
    const inkN = ctx.measureText("n").actualBoundingBoxRight / 100;
    this.em = {
      cap,
      base:
        (1 + (one.fontBoundingBoxAscent - one.fontBoundingBoxDescent) / 100) /
        2,
      inkA,
      inkN,
      box,
      markW: 2 * INK_W * box,
      padL: GAP - (A[3] - inkN),
      padR: GAP - inkA,
    };

    const chars: [string, number, Kind][] = [
      ["a", A[0], "end"],
      ["1", A[1], "digit"],
      ["3", A[2], "digit"],
      ...[...FULL.slice(1, -1)].map((ch, k): [string, number, Kind] => [
        ch,
        B[k + 1],
        "mid",
      ]),
      ["n", A[3], "end"],
    ];
    this.glyphs = chars.map(([ch, adv, kind], i) => {
      const el = document.createElement("span");
      el.className = "g";
      el.textContent = ch;
      const fold = new Spring(kind === "mid" ? 90 : 0, 170, 17);
      return { el, adv, kind, fold, sign: i % 2 ? -1 : 1, ox: 0 };
    });
    $("#glyphs").replaceChildren(...this.glyphs.map((g) => g.el));
    [this.shadowLayer, this.faceLayer] = this.mark.querySelectorAll("svg");
    this.name.append(this.mark);

    const gl = this.glyphs;
    this.shut = this.chain(
      0,
      gl.map((g) => (g.kind === "mid" ? 0 : 1)),
    );
    this.full = this.chain(
      gl.length,
      gl.map((g) => (g.kind === "digit" ? 0 : 1)),
    );
    this.space = gl.findIndex((g) => g.el.textContent === " ");
    this.first = this.chain(
      this.space,
      gl.map((g, i) => (g.kind !== "digit" && i < this.space ? 1 : 0)),
    );
    this.second = gl.reduce((w, g, i) => w + (i > this.space ? g.adv : 0), 0);

    this.layout();
    this.listen();
    // a first visit sees it unfold once, then fold back
    setTimeout(() => this.touched || this.setOpen(true), 900);
    setTimeout(() => this.touched || this.setOpen(false), 3900);
  }

  /** Fits the name to the measure; call on resize. */
  layout() {
    const fr = this.pitch.getBoundingClientRect();
    const fs = getComputedStyle(this.pitch);
    this.left =
      fr.left +
      parseFloat(fs.paddingLeft) -
      this.name.getBoundingClientRect().left;
    this.room =
      fr.width - parseFloat(fs.paddingLeft) - parseFloat(fs.paddingRight);
    const two = this.room < NARROW;
    if (two !== this.two && this.open) this.setOpen(false);
    this.two = two;
    // open, the name fills the measure; closed, it is ZOOM times that size,
    // held to a share of the screen's height
    const zoom = two ? ZOOM_NARROW : ZOOM;
    this.size = Math.round(
      Math.min(
        (zoom * this.room) / (this.full.r - this.full.l),
        this.section.clientHeight * 0.225,
      ),
    );
    this.name.style.setProperty("--s", `${this.size}px`);
    this.ground = GROUND * this.size;
    const shutW = this.shut.r - this.shut.l;
    const openW = two
      ? Math.max(this.first.r - this.first.l, this.second)
      : this.full.r - this.full.l;
    const shut = this.camera(shutW);
    const open = this.camera(openW);
    // the box holds the closed name, the open one, the mark's turn, and the
    // gap below
    const capShut = this.em.cap * shut.scale;
    const rise = Math.max(capShut, two ? (LEAD + this.em.cap) * open.scale : 0);
    this.height = Math.round(this.ground + rise + 0.14 * this.size);
    this.name.style.height = `${this.height}px`;
    // the closed name keeps the middle of the screen; the risen first line
    // takes the room above it
    this.name.style.marginTop = `${-Math.round(rise - capShut)}px`;
    // each glyph turns about its own vertical center line
    for (const g of this.glyphs) {
      g.ox = ((g.adv - TRACK) / 2) * this.size;
      g.el.style.transformOrigin = `${g.ox.toFixed(1)}px 0`;
    }
    this.mark.style.width =
      this.mark.style.height = `${this.size * this.em.box}px`;
    // the zone spans the closed lockup and the open one, so the zoom never
    // moves its edge under a resting pointer
    const half = Math.max(shutW * shut.scale, openW * open.scale) / 2;
    const mid = this.left + this.room / 2;
    const baseY = this.height - this.ground;
    this.zone = {
      l: mid - half,
      r: mid + half,
      t: baseY - rise,
      b: baseY + 0.25 * shut.scale,
    };
    this.dirty = true;
  }

  tick(now: number, dt: number) {
    while (this.queue.length && this.queue[0].at <= now)
      this.queue.shift()?.run();
    // anticipation: the closed 1 and 3 lean open as the pointer comes near
    if (!this.open && this.settled)
      for (const g of this.glyphs.slice(1, 3)) g.fold.t = 22 * this.near;
    let moving = this.dirty || this.wave !== null;
    for (const g of this.glyphs) moving = g.fold.update(dt) || moving;
    moving = this.lift.update(dt) || moving;
    this.moveCursor();
    this.shadow.t = this.turn;
    moving = this.shadow.update(dt) || moving;
    if (moving) this.render();
    this.dirty = false;
    this.followScroll();
  }

  // ---- triggers: scroll on, hover the name, tap it, or press Enter ----

  private listen() {
    addEventListener(
      "pointermove",
      (e) => {
        if (e.pointerType !== "mouse") return;
        this.pointer = e;
        const d = this.distance(e);
        this.near = clamp(1 - d / LEAN_FAR, 0, 1);
        // leaving folds it back at once, unless the page is scrolled on
        if (d < OPEN_NEAR) this.touch(true);
        else if (d > CLOSE_FAR && !this.scrolled) this.setOpen(false);
      },
      { passive: true },
    );
    // a lifted finger leaves the page too; only the mouse leaving counts
    document.documentElement.addEventListener("pointerleave", (e) => {
      if (e.pointerType !== "mouse") return;
      this.near = 0;
      this.pointer = null;
      if (!this.scrolled) this.setOpen(false);
    });
    // the mouse opens it by hovering; touch and pen tap it
    this.name.addEventListener("click", (e) => {
      if (e instanceof PointerEvent && e.pointerType === "mouse") return;
      this.touch(!this.open);
    });
    this.name.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      this.touch(!this.open);
    });
  }

  private touch(open: boolean) {
    this.touched = true;
    this.setOpen(open);
  }

  // scrolling on opens the name; back at the top it folds, unless the
  // pointer rests on it
  private followScroll() {
    const down = scrollY > SCROLLED;
    if (down === this.scrolled) return;
    this.scrolled = down;
    this.touch(
      down ||
        (this.pointer !== null && this.distance(this.pointer) < CLOSE_FAR),
    );
  }

  private distance(e: PointerEvent) {
    const r = this.name.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    const z = this.zone;
    return Math.hypot(
      Math.max(z.l - x, 0, x - z.r),
      Math.max(z.t - y, 0, y - z.b),
    );
  }

  // ---- folding ----

  private later(ms: number, run: () => void) {
    this.queue.push({ at: performance.now() + (reduced ? 0 : ms), run });
    this.queue.sort((p, q) => p.at - q.at);
  }

  private unfold(glyphs: Glyph[], step: number) {
    this.wave = { glyphs, step: reduced ? 0 : step, next: 0 };
  }

  // on two lines, "foundation" lies beyond the mark, below the risen first line
  private beyond(i: number) {
    return this.two && i > this.space;
  }

  private lifted() {
    return this.two && this.lift.x > 0.01;
  }

  private setOpen(open: boolean) {
    if (open === this.open) return;
    this.open = open;
    this.settled = false;
    this.home = false;
    this.queue = [];
    this.wave = null;
    this.name.setAttribute("aria-expanded", String(open));
    const gl = this.glyphs;
    // fold away what the other form hides, and what stands between the mark
    // and its end of the line, nearest the mark first
    const hide = open ? "digit" : "mid";
    const fold = open
      ? gl.filter((g) => g.kind !== "mid")
      : [...gl].reverse().filter((g) => g.kind !== "digit");
    const step = open ? 26 : 22;
    fold.forEach((g, k) =>
      this.later(k * step, () => {
        const i = gl.indexOf(g);
        const ahead = open
          ? i >= this.cursor
          : i < this.cursor || this.beyond(i);
        if (g.kind === hide || ahead) g.fold.t = 90;
      }),
    );
    if (open)
      this.unfold(
        gl.filter(
          (g, i) => g.kind !== "digit" && !(this.two && i === this.space),
        ),
        24,
      );
    this.dirty = true;
  }

  // the mark crosses a glyph only while that glyph is edge-on and stays so
  private moveCursor() {
    const gl = this.glyphs;
    const flat = (g: Glyph) =>
      g.fold.t === 90 && Math.cos(g.fold.x * DEG) < EDGE;
    // on two lines the mark ends the first line
    const end = this.two ? this.space : gl.length;
    if (this.open)
      while (this.cursor < end && flat(gl[this.cursor])) this.cursor++;
    else while (this.cursor > 0 && flat(gl[this.cursor - 1])) this.cursor--;
    // the first line comes back down once "foundation" has folded
    if (
      !this.open &&
      this.lift.t &&
      gl.every((g, i) => !this.beyond(i) || flat(g))
    )
      this.lift.t = 0;
    // home again: a13n opens out of the mark
    if (!this.open && !this.home && this.cursor === 0) {
      this.home = true;
      this.unfold(
        gl.filter((g) => g.kind !== "mid"),
        40,
      );
      this.later(520, () => (this.settled = true));
    }
    const w = this.wave;
    const now = performance.now();
    while (w && w.glyphs.length && now >= w.next) {
      const i = gl.indexOf(w.glyphs[0]);
      if (this.open && this.beyond(i)) {
        // "foundation" waits for the mark to end the first line, and for
        // the first line to rise
        if (this.cursor < this.space) break;
        this.lift.t = 1;
        if (this.lift.x < 0.92) break;
      } else if (
        this.open
          ? i >= this.cursor
          : i < this.cursor || (this.beyond(i) && this.lifted())
      )
        break;
      w.glyphs[0].fold.t = 0;
      w.glyphs.shift();
      w.next = now + w.step;
    }
    if (w && !w.glyphs.length) this.wave = null;
  }

  // ---- layout and drawing ----

  // each glyph is as wide as it is turned toward us, and the mark sits
  // before glyph c, keeping its gap only on a side with letters
  private chain(c: number, cos: number[]): Line {
    const { em, glyphs: gl } = this;
    let wl = 0;
    let wr = 0;
    gl.forEach((g, i) =>
      i < c ? (wl += g.adv * cos[i]) : (wr += g.adv * cos[i]),
    );
    const pl = em.padL * smooth(0, 0.15, wl);
    const pr = em.padR * smooth(0, 0.15, wr);
    const xs: number[] = [];
    let x = 0;
    let at = 0;
    for (let i = 0; i <= gl.length; i++) {
      if (i === c) {
        at = x + pl + em.markW / 2;
        x += pl + em.markW + pr;
      }
      if (i < gl.length) {
        xs.push(x);
        x += gl[i].adv * cos[i];
      }
    }
    const n = gl.length - 1;
    return {
      xs,
      at,
      l: Math.min(at - em.markW / 2, xs[0] + em.inkA * cos[0]),
      r: Math.max(at + em.markW / 2, xs[n] + em.inkN * cos[n]),
    };
  }

  // the camera fits the widest line to the measure, never past the closed
  // lockup; zoom is the scale against the closed name's
  private camera(w: number) {
    const scale = Math.min(
      this.size,
      this.room / Math.max(w, this.shut.r - this.shut.l),
    );
    return { scale, zoom: scale / this.size };
  }

  // each line stands centered on the page
  private center(l: number, r: number, scale: number) {
    return this.left + this.room / 2 - ((l + r) / 2) * scale;
  }

  private render() {
    const { em, glyphs: gl } = this;
    const cos = gl.map((g) => Math.max(Math.cos(g.fold.x * DEG), 0));
    const up = this.lifted() ? this.lift.x : 0;
    const one = this.chain(
      this.cursor,
      up ? cos.map((v, i) => (i < this.space ? v : 0)) : cos,
    );
    const two = { xs: [] as number[], w: 0 };
    if (up)
      gl.forEach((g, i) => {
        two.xs.push(two.w);
        if (this.beyond(i)) two.w += g.adv * cos[i];
      });
    // the first line steps back as it rises, so "foundation" unfolds at its
    // final size
    const { scale, zoom } = this.camera(
      Math.max(one.r - one.l, this.second * up),
    );
    const x1 = this.center(one.l, one.r, scale);
    const x2 = this.center(0, two.w, scale);
    // open or closed, the name stands on the same baseline
    const baseY = this.height - this.ground;
    const y1 = baseY - up * LEAD * scale;
    const z = zoom.toFixed(4);
    gl.forEach((g, i) => {
      const below = up && this.beyond(i);
      const x = below ? x2 + two.xs[i] * scale : x1 + one.xs[i] * scale;
      const top = (below ? baseY : y1) - em.base * scale;
      const angle = g.sign * g.fold.x;
      const tx = x + ((g.adv - TRACK) / 2) * cos[i] * scale - g.ox;
      g.el.style.transform = `translate3d(${tx.toFixed(2)}px,${top.toFixed(2)}px,0) scale3d(${z},${z},${z}) rotateY(${angle.toFixed(2)}deg)`;
      g.el.style.opacity = clamp(cos[i] * 3.2, 0, 1).toFixed(3);
      // light from the left: pleats facing it pale, pleats facing away darken
      const tone = Math.sin(angle * DEG);
      const ink = g.kind === "mid" ? GRAY : INK;
      g.el.style.color = rgb(
        mix(
          mix(ink, PAPER, Math.max(0, -tone) * 0.55),
          INK,
          Math.max(0, tone) * 0.45,
        ),
      );
    });
    // the mark rolls on the baseline: one turn over the whole way
    const end = this.two ? this.first : this.full;
    this.turn =
      360 * clamp((one.at - this.shut.at) / (end.at - this.shut.at), 0, 1);
    const half = (this.size * em.box * zoom) / 2;
    const cx = x1 + one.at * scale;
    const cy = y1 - (em.cap / 2) * scale;
    this.mark.style.transform = `translate3d(${(cx - half).toFixed(2)}px,${(cy - half).toFixed(2)}px,0) scale(${z})`;
    this.faceLayer.style.rotate = `${this.turn.toFixed(2)}deg`;
    this.shadowLayer.style.rotate = `${this.shadow.x.toFixed(2)}deg`;
  }
}
