import frameUrl from "./mermaid-frame-url";
import {
  maxTextSize,
  type FrameReply,
  type FrameRequest,
} from "./mermaid-protocol";

const failureMessages = {
  "too-long": "Diagram source exceeds 50,000 characters.",
  "too-many-edges": "Diagram exceeds 500 connections.",
  "unsupported-directive":
    "Diagram configuration directives are not supported in previews.",
  "unsupported-type": "This Mermaid diagram type is not recognized.",
  "image-unavailable":
    "An image in this diagram could not load. External images are disabled in previews.",
  "invalid-diagram": "Mermaid could not render this diagram. Check its syntax.",
  "timed-out": "Diagram rendering timed out.",
  unavailable: "The Mermaid preview renderer could not start.",
} as const;

type FailureReason = (typeof failureMessages)[keyof typeof failureMessages];
type DiagramResult =
  { src: string; reason?: never } | { src?: never; reason: FailureReason };

// Mermaid shares mutable parser and theme state. Send one job at a time.
let queue: Promise<unknown> = Promise.resolve();
let iframe: HTMLIFrameElement | undefined;
let serial = 0;
let unavailableThrough = 0;
const timeoutMs = 8_000;

// Mermaid emits width="100%" without a height, which gives an <img> a 300×150
// default box and shrinks tall diagrams until their labels are unreadable.
// Pin the layout's own size so the image renders at 1:1 and only scales down
// to fit the column width.
export function intrinsicSize(svg: string) {
  const root = new DOMParser().parseFromString(
    svg,
    "image/svg+xml",
  ).documentElement;
  const box = root
    .getAttribute("viewBox")
    ?.trim()
    .split(/[\s,]+/)
    .map(Number);
  if (
    root.nodeName !== "svg" ||
    box?.length !== 4 ||
    !box.every(Number.isFinite)
  )
    return svg;
  root.setAttribute("width", String(Math.ceil(box[2])));
  root.setAttribute("height", String(Math.ceil(box[3])));
  root.style.removeProperty("max-width");
  return new XMLSerializer().serializeToString(root);
}

function renderInFrame(
  id: number,
  source: string,
  dark: boolean,
): Promise<DiagramResult> {
  const start = !iframe;
  const frame = iframe ?? document.createElement("iframe");
  const request: FrameRequest = { kind: "render-mermaid", id, source, dark };
  return new Promise((resolve) => {
    let ready = !start;
    let deadline = performance.now() + timeoutMs;
    let timer = window.setTimeout(expire, timeoutMs);
    function expire() {
      frame.remove();
      iframe = undefined;
      // Fail the queued batch once when the preview asset cannot start. A
      // later request can retry; a render timeout only fails its own job.
      if (!ready) unavailableThrough = serial;
      finish({ reason: failureMessages[ready ? "timed-out" : "unavailable"] });
    }
    function finish(result: DiagramResult) {
      window.clearTimeout(timer);
      window.removeEventListener("message", receive);
      resolve(result);
    }
    function send() {
      // Opaque origins cannot be specified as postMessage targets.
      frame.contentWindow!.postMessage(request, "*");
    }
    function receive({
      origin,
      source: sender,
      data,
    }: MessageEvent<FrameReply>) {
      if (origin !== "null" || sender !== frame.contentWindow) return;
      if (performance.now() >= deadline) {
        expire();
        return;
      }
      if (!ready && data?.kind === "mermaid-ready") {
        ready = true;
        deadline = performance.now() + timeoutMs;
        window.clearTimeout(timer);
        timer = window.setTimeout(expire, timeoutMs);
        send();
      } else if (ready && data?.kind === "mermaid-result" && data.id === id) {
        if (data.reason) finish({ reason: failureMessages[data.reason] });
        else if (typeof data.svg === "string")
          finish({
            src: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(intrinsicSize(data.svg))}`,
          });
      }
    }
    window.addEventListener("message", receive);
    if (start) {
      frame.setAttribute("sandbox", "allow-scripts");
      frame.setAttribute("aria-hidden", "true");
      frame.tabIndex = -1;
      // Keep the document laid out for text measurement without affecting the app.
      frame.style.cssText =
        "position:fixed;inset:0;width:100%;height:100%;border:0;visibility:hidden;pointer-events:none";
      frame.src = frameUrl;
      document.body.append(frame);
      iframe = frame;
    } else send();
  });
}

export function renderDiagram(
  source: string,
  dark: boolean,
): Promise<DiagramResult> {
  if (source.length > maxTextSize)
    return Promise.resolve({ reason: failureMessages["too-long"] });
  // Keep the application's theme and limits; diagram statements go to Mermaid untouched.
  if (/^\s*---[ \t]*(?:\r?\n|$)|%%\s*\{/.test(source))
    return Promise.resolve({
      reason: failureMessages["unsupported-directive"],
    });
  const id = ++serial;
  const result = queue.then(() =>
    id <= unavailableThrough
      ? { reason: failureMessages.unavailable }
      : renderInFrame(id, source, dark),
  );
  queue = result.catch(() => {});
  return result.catch(() => ({ reason: failureMessages.unavailable }));
}
