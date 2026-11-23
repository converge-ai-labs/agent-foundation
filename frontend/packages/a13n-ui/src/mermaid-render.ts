// Mermaid owns its parser/layout. Load it only when a complete diagram is shown.
// Its global configuration/render state must not race across messages or themes.
let queue: Promise<unknown> = Promise.resolve();
let serial = 0;
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
const maxTextSize = 50_000;
const staticDiagram =
  /^(?:flowchart|graph|sequenceDiagram|stateDiagram-v2)(?=\s|;|$)/;
const authoredSettings = /^\s*---|%%\s*\{/m;
const authoredAssets = /@\s*\{|\b(?:style|classDef|linkStyle)\b/i;
const sequenceAssets = /\b(?:properties|details|rect)\b/i;
const richLabels = /<\/?[a-z!]|\$\$|&(?:lt|#0*60|#x0*3c)|#(?:lt|60|x3c);/i;

function supportsStaticLayout(source: string) {
  const body = source.replace(/^\s*(?:%%[^\r\n]*(?:\r\n?|\n)\s*)*/, "");
  return (
    staticDiagram.test(body) &&
    !authoredSettings.test(source) &&
    !authoredAssets.test(source) &&
    !(body.startsWith("sequenceDiagram") && sequenceAssets.test(body)) &&
    !richLabels.test(body.replace(/<<(?:fork|join|choice)>>/gi, ""))
  );
}

export function renderDiagram(source: string, dark: boolean): Promise<string> {
  // Strict sanitizes the serialized SVG, but Mermaid lays it out in the live
  // document first. Admit only the reviewed text diagram types, without
  // directives, custom styles, assets, actor metadata or rich labels.
  if (source.length > maxTextSize || !supportsStaticLayout(source))
    return Promise.reject(new Error("Diagram requires source view."));
  const render = queue.then(async () => {
    const { default: mermaid } = await import("mermaid");
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      suppressErrorRendering: true,
      maxTextSize,
      maxEdges: 500,
      theme: "base",
      htmlLabels: false,
      flowchart: { htmlLabels: false, curve: "basis", padding: 18 },
      fontFamily: "system-ui, sans-serif",
      themeVariables: {
        darkMode: dark,
        fontSize: "14px",
        primaryColor: dark ? "#27272a" : "#f4f4f5",
        primaryTextColor: dark ? "#e4e4e7" : "#27272a",
        primaryBorderColor: dark ? "#71717a" : "#a1a1aa",
        lineColor: dark ? "#a1a1aa" : "#71717a",
        secondaryColor: dark ? "#243044" : "#eff6ff",
        tertiaryColor: dark ? "#202024" : "#fafafa",
        background: dark ? "#18181b" : "#ffffff",
      },
    });
    // Mermaid needs a laid-out DOM for text measurement. Keep its temporary
    // SVG out of document flow so opening a diagram cannot scroll the workbench.
    const host = document.createElement("div");
    host.style.cssText =
      "position:fixed;inset:0;overflow:hidden;visibility:hidden;pointer-events:none";
    document.body.append(host);
    try {
      const { svg } = await mermaid.render(`diagram-${++serial}`, source, host);
      // An image document cannot execute diagram callbacks or inject styles into
      // the application. Do not bind Mermaid's optional interactive callbacks.
      return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(intrinsicSize(svg))}`;
    } finally {
      host.remove();
    }
  });
  queue = render.catch(() => {});
  return render;
}
