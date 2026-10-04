export const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

/** The page's easing: a quick start and a long, soft landing. */
export const EASE = "cubic-bezier(0.16, 1, 0.3, 1)";
export const DEG = Math.PI / 180;

export type Point = [x: number, y: number];

export const clamp = (v: number, a: number, b: number) =>
  Math.min(Math.max(v, a), b);

/** 0 up to a, 1 from b, smooth in between. */
export function smooth(a: number, b: number, v: number) {
  const t = clamp((v - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
}

export const easeInOut = (t: number) =>
  t < 0.5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2;

export const rand = (a: number, b: number) => a + Math.random() * (b - a);

/** A point m of the way from p to q on a path that leaves p level, then turns toward q. */
export function leg([x0, y0]: Point, [x1, y1]: Point, m: number): Point {
  const q = 1 - m;
  return [
    q * q * x0 + 2 * q * m * x0 + m * m * x1,
    q * q * y0 + 2 * q * m * y1 + m * m * y1,
  ];
}

/** A damped spring: x follows its target t. */
export class Spring {
  x: number;
  t: number;
  v = 0;
  private readonly k: number;
  private readonly c: number;

  constructor(value: number, k = 200, c = 26) {
    this.x = this.t = value;
    this.k = k;
    this.c = c;
  }

  get rest() {
    return Math.abs(this.x - this.t) < 1e-3 && Math.abs(this.v) < 1e-2;
  }

  /** Moves dt seconds toward the target; false when it was already at rest. */
  update(dt: number) {
    if (this.rest) return false;
    if (reduced) {
      this.x = this.t;
      this.v = 0;
      return true;
    }
    // two half steps keep a stiff spring stable on a slow frame
    for (let k = 0; k < 2; k++) {
      const a = -this.k * (this.x - this.t) - this.c * this.v;
      this.v += (a * dt) / 2;
      this.x += (this.v * dt) / 2;
    }
    return true;
  }
}
