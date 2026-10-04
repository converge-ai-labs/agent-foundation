import { $ } from "../dom";
import { clamp, easeInOut, leg, type Point, reduced, smooth } from "../motion";
import type { Build } from "./build";
import type { Call } from "./call";
import type { Run } from "./run";

const NAV = 64; // the nav bar, in px
// the scene is 440vh tall, so its stage stays pinned for 340vh of scroll;
// the timeline below counts in those vh
const V = (vh: number) => vh / 340;

/** A statement that steps back: it rises, fades and blurs away. */
function stepBack(el: HTMLElement, a: number) {
  el.style.opacity = (1 - a).toFixed(3);
  el.style.transform = a ? `translateY(${(-28 * a).toFixed(1)}px)` : "";
  el.style.filter = a ? `blur(${(8 * a).toFixed(2)}px)` : "";
}

/**
 * The Service scene: one pinned stage in three acts, played by the scroll.
 * The agent folds into a point of light, the room goes dark, the light
 * becomes the Service that code calls, and then it lands in the wall of
 * sessions and lights it.
 */
export class Stage {
  /** Whether the night is under the nav bar. */
  dark = false;

  private readonly scene = $("#service");
  private readonly stage = $("#stage");
  private readonly night = $("#night");
  private readonly light = $("#light");
  private readonly flyer = $("#flyer");
  private readonly build: Build;
  private readonly call: Call;
  private readonly run: Run;
  private sp = 0; // eased progress, 0 to 1
  // the light's way, in px on the stage: from the agent's name, to the
  // Service, to the wall
  private from: Point = [0, 0];
  private mid: Point = [0, 0];
  private to: Point = [0, 0];
  private far = 0; // from the agent's name to the stage's farthest corner

  constructor(build: Build, call: Call, run: Run) {
    this.build = build;
    this.call = call;
    this.run = run;
    this.layout();
  }

  /** Finds the light's way across the acts; call on resize, after them. */
  layout() {
    // positions on the stage, free of any transforms
    const on = (el: HTMLElement): Point => {
      let x = 0;
      let y = 0;
      for (
        let n: HTMLElement | null = el;
        n && n !== this.stage;
        n = n.offsetParent as HTMLElement | null
      ) {
        x += n.offsetLeft;
        y += n.offsetTop;
      }
      return [x, y];
    };
    const { name, sheet } = this.build;
    const [nx, ny] = on(name);
    this.from = [nx, ny + name.offsetHeight / 2];
    sheet.style.setProperty("--ox", `${nx - on(sheet)[0]}px`);
    const node = this.call.node;
    const [mx, my] = on(node);
    this.mid = [mx + node.offsetWidth / 2, my + node.offsetHeight / 2];
    const [cx, cy] = on(this.run.canvas);
    this.to = [cx + this.run.seed[0], cy + this.run.seed[1]];
    const { clientWidth: w, clientHeight: h } = this.stage;
    this.far = Math.hypot(
      Math.max(nx, w - nx),
      Math.max(this.from[1], h - this.from[1]),
    );
  }

  tick(now: number, dt: number) {
    const { build, call, run } = this;
    const r = this.scene.getBoundingClientRect();
    if (r.bottom <= 0 || r.top >= innerHeight) {
      run.on = call.on = this.dark = false;
      return;
    }
    const p = clamp(-r.top / (r.height - innerHeight), 0, 1);
    this.sp = reduced ? p : this.sp + (p - this.sp) * (1 - Math.exp(-dt * 9));
    const sp = this.sp;
    const span = (a: number, b: number) => smooth(V(a), V(b), sp);

    // act one: the agent folds into a point of light
    stepBack(build.title, span(55, 75));
    // rows fold away from the bottom up, like the pleats of the name
    build.rows.forEach((row, j) => {
      const f = span(55 + j * 5.5, 73 + j * 5.5);
      row.style.transform = f ? `rotateX(${(-90 * f).toFixed(2)}deg)` : "";
      row.style.opacity = (1 - f).toFixed(3);
    });
    // the name draws back into its first letter, which becomes the light
    build.sheet.style.setProperty("--c", span(86, 101).toFixed(4));
    build.layer.style.visibility = sp > V(125) ? "hidden" : "visible";
    // night spreads out from the point the agent became
    const dark = span(92, 122);
    const [fx, fy] = this.from;
    this.night.style.clipPath = dark
      ? `circle(${(dark * this.far).toFixed(1)}px at ${fx.toFixed(1)}px ${fy.toFixed(1)}px)`
      : "circle(0 at 0 0)";
    // the bar turns dark once the night covers both of its top corners
    this.dark =
      dark * this.far > Math.hypot(Math.max(fx, r.width - fx), fy) &&
      r.top <= 0 &&
      r.bottom > NAV;

    // the light goes to the Service and becomes its mark; later the mark
    // rolls down into the wall, one turn by distance, and lights it
    const m1 = easeInOut(span(101, 128));
    const m2 = easeInOut(span(214, 238));
    const [x, y] = m2 ? this.to : leg(this.from, this.mid, m1);
    const grow = m2 ? 1 + 0.6 * span(234, 246) : 1 - 0.4 * span(118, 130);
    this.light.style.opacity = (
      m2
        ? span(234, 238) * (1 - span(242, 252))
        : span(92, 101) * (1 - span(124, 130))
    ).toFixed(3);
    this.light.style.transform = `translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,0) scale(${grow.toFixed(3)})`;
    const [mx, my] = leg(this.mid, this.to, m2);
    this.flyer.style.opacity = m2 ? (1 - span(235, 239)).toFixed(3) : "0";
    this.flyer.style.transform = `translate3d(${mx.toFixed(1)}px,${my.toFixed(1)}px,0) rotate(${(360 * m2).toFixed(1)}deg) scale(${(1 + 1.2 * Math.sin(Math.PI * m2)).toFixed(3)})`;

    // act two: code calls the Service
    const out = span(202, 216);
    call.on = sp > V(108) && sp < V(240);
    call.layer.style.visibility = call.on ? "visible" : "hidden";
    call.layer.style.pointerEvents =
      sp > V(136) && sp < V(202) ? "auto" : "none";
    if (sp > V(114)) call.title.classList.add("in");
    else if (sp < V(108)) call.title.classList.remove("in");
    stepBack(call.title, out);
    call.parts.forEach((el, k) => {
      const f = span(118 + k * 4, 136 + k * 4);
      el.style.opacity = (f * (1 - out)).toFixed(3);
      el.style.transform =
        f < 1 || out
          ? `translate3d(0,${(16 * (1 - f) - 14 * out).toFixed(1)}px,0)`
          : "";
    });
    // the mark outlasts the act, then hands over to the one that flies
    const mark = sp < V(214) ? span(124, 130) : 0;
    call.node.style.opacity = mark.toFixed(3);
    call.node.style.transform =
      mark < 1 ? `scale(${(0.4 + 0.6 * mark).toFixed(3)})` : "";
    call.active = sp > V(128) && sp < V(204);
    call.deploy.style.opacity = (span(130, 146) * (1 - out)).toFixed(3);

    // act three: the wall, lit from where the light lands
    if (sp > V(230)) run.title.classList.add("in");
    else if (sp < V(224)) run.title.classList.remove("in");
    run.layer.style.visibility = sp < V(224) ? "hidden" : "visible";
    run.layer.style.pointerEvents = sp > V(284) ? "auto" : "none";
    run.reveal = span(238, 284);
    run.on = run.reveal > 0;
    const parts = span(260, 282).toFixed(3);
    for (const el of run.parts) el.style.opacity = parts;

    build.tick(now, sp < V(55));
  }
}
