import type { ReactNode } from "react";
import { logo } from "./brand";
import s from "./runtime-map.module.css";

export function ServiceMark({
  x,
  y,
  size,
}: {
  x: number;
  y: number;
  size: number;
}) {
  return (
    <image
      href={logo.src}
      x={x}
      y={y}
      width={size}
      height={size}
      aria-hidden="true"
    />
  );
}

// Symbols follow electrical-schematic conventions: a port, a cylinder, and
// revision sheets. Labels and targets live in the diagram rather than the art.
export function ApiPort() {
  return (
    <g aria-hidden="true" fill="none" strokeLinejoin="miter">
      <path
        d="M-29-24h49l9 9v39h-58z"
        fill="var(--field)"
        stroke="var(--request-wire)"
        strokeWidth="1.5"
      />
      <path d="M-29-24h6v48h-6z" fill="var(--request-wire)" />
      <path
        d="M-13-8h27m-8-7 8 7-8 7"
        stroke="var(--request-wire)"
        strokeWidth="3.7"
      />
      <path d="M14 10h-26m6-5-6 5 6 5" stroke="var(--ink)" strokeWidth="2" />
    </g>
  );
}

export function RunStore() {
  return (
    <g
      aria-hidden="true"
      stroke="var(--store-stroke)"
      strokeWidth="1.6"
      fill="none"
    >
      <path
        d="M-30-16c0-14 60-14 60 0v34c0 14-60 14-60 0z"
        fill="var(--field)"
      />
      <ellipse cy="-16" rx="30" ry="10" fill="var(--mint)" />
      <path d="M-30-5c0 14 60 14 60 0M-30 7c0 14 60 14 60 0" />
      <path d="M-24 17v4m4-3v4" stroke="var(--state-wire)" strokeWidth="2" />
    </g>
  );
}

export function CheckpointStore() {
  return (
    <g
      aria-hidden="true"
      stroke="var(--store-stroke)"
      strokeWidth="1.5"
      fill="var(--field)"
    >
      <path d="M-28-17h32l10 10v32h-42z" />
      <path d="M-20-23h32l10 10v32h-42z" />
      <path d="M-12-29h32l10 10v32h-42z" />
      <path d="M20-29v10h10" fill="var(--mint)" />
      <path d="M-5-9h23m-23 6h16" fill="none" stroke="var(--state-wire)" />
      <path d="m-5 6 3.5-3.5L2 6l-3.5 3.5z" fill="var(--mint)" stroke="none" />
    </g>
  );
}

// A dispatch and its optional return path form the Harness execution glyph.
export function HarnessLoop({
  x = 0,
  y = 0,
  size = 36,
}: {
  x?: number;
  y?: number;
  size?: number;
}) {
  return (
    <svg
      x={x}
      y={y}
      width={size}
      height={size}
      viewBox="0 0 36 36"
      aria-hidden="true"
      color="inherit"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinejoin="round"
    >
      <path d="M6 21h20m-5-4 5 4-5 4M29 17v-5q0-4-4-4H13q-4 0-4 4v4m-3-3 3 3 3-3" />
      <rect
        x="3"
        y="18"
        width="6"
        height="6"
        fill="currentColor"
        stroke="none"
      />
      <circle cx="29" cy="21" r="2.8" />
    </svg>
  );
}

export function TraceWindow({
  width,
  height,
}: {
  width: number;
  height: number;
}) {
  return (
    <svg width={width} height={height} viewBox="0 0 142 64" aria-hidden="true">
      <rect
        x="1"
        y="1"
        width="140"
        height="62"
        rx="3"
        fill="var(--field)"
        stroke="var(--frame)"
        strokeWidth="1.1"
      />
      <path
        d="M1 13h140m-119 0v49M8 23h8m-8 6h6m-6 5h7"
        fill="none"
        stroke="var(--frame)"
      />
      <text x="131" y="9.5" textAnchor="end" className={s.streamTag}>
        SSE
      </text>
      {[8, 14, 20].map((x) => (
        <circle key={x} cx={x} cy="7" r="1.35" fill="var(--quiet)" />
      ))}
      <path d="M29 23v28m0-15h11m-11 14h20" stroke="var(--frame)" fill="none" />
      <g strokeWidth="4">
        <path d="M33 23h65" stroke="var(--request-wire)" />
        <path d="M42 36h22m-14 14h33" stroke="var(--ink)" />
      </g>
      <path d="M126 20h5v5h-5z" fill="var(--request-wire)" />
    </svg>
  );
}

export function RequestWindow({
  width,
  height,
}: {
  width: number;
  height: number;
}) {
  return (
    <svg width={width} height={height} viewBox="0 0 142 58" aria-hidden="true">
      <rect width="142" height="58" rx="3" fill="var(--terminal)" />
      <path d="M0 1h44" stroke="var(--request-wire)" strokeWidth="2" />
      <text x="11" y="21" className={s.method}>
        POST
      </text>
      <text x="11" y="43" className={s.endpoint}>
        /api/v1/threads
      </text>
    </svg>
  );
}

const glyphs = {
  model: (
    <>
      <path d="M12 2v20M2 12h20M5 5l14 14M19 5 5 19" strokeWidth="2.5" />
      <circle cx="12" cy="12" r="3" fill="currentColor" />
    </>
  ),
  tools: (
    <>
      <path d="m8 5-6 7 6 7m8-14 6 7-6 7m-3-15-2 17" />
    </>
  ),
  environment: (
    <>
      <rect x="2" y="4.5" width="20" height="15" rx="3" />
      <path d="m6 9 3 3-3 3m6 0h5" />
    </>
  ),
  skills: (
    <>
      <path d="M3 5q5-2 9 1 4-3 9-1v14q-5-2-9 1-4-3-9-1Zm9 1v14" />
    </>
  ),
  memory: (
    <>
      <path d="m2 8 10-5 10 5-10 5Zm1 5 9 4.5 9-4.5m-18 4 9 4.5 9-4.5" />
    </>
  ),
  code: <path d="m7 7-5 5 5 5m10-10 5 5-5 5m-3-13-4 18" />,
} satisfies Record<string, ReactNode>;

export function Glyph({
  kind,
  x = 0,
  y = 0,
  size = 20,
}: {
  kind: keyof typeof glyphs;
  x?: number;
  y?: number;
  size?: number;
}) {
  return (
    <svg
      x={x}
      y={y}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {glyphs[kind]}
    </svg>
  );
}
