"use client";
import { useTheme } from "next-themes";
import { useEffect, useId, useState } from "react";

// Wide diagrams shrink to fit, but not below this share of their natural size; beyond it they scroll.
const MIN_SCALE = 0.75;

// Sequence and state arrowheads, drawn larger than the 8px flowchart ones, shrink to this share to match.
const ARROW_SCALE = 0.7;

/*
 * Node categories that diagram sources assign with `class Node a13n`. GitHub
 * renders the same source without these colors, so a category adds meaning
 * but is never required to read a diagram.
 */
const categories: Record<string, string> = {
  a13n: "var(--color-violet-500)",
  app: "var(--color-teal-500)",
  store: "var(--color-orange-500)",
  ext: "var(--color-pink-500)",
  success: "var(--success)",
  warning: "var(--warning)",
  danger: "var(--destructive)",
};

// Sequence diagrams have no class statement, so their sources assign categories in comments: `%% class Agent,Worker a13n`.
const classComment = /^\s*%%\s*class\s+(\S+)\s+(\w+)\s*$/gm;

// Mermaid derives colors from these values, so they are literal colors per theme.
const palettes = {
  light: {
    card: "#ffffff",
    ring: "#dcdde2",
    text: "#16171a",
    muted: "#6b6f78",
    line: "#a0a4ad",
    lifeline: "#e2e3e7",
    note: "#fff7e5",
    noteRing: "#f0d590",
    activation: "#f1ecff",
    activationRing: "#b9a0f7",
    shadow: "drop-shadow(0 1px 1.5px rgba(16, 18, 24, 0.07))",
  },
  dark: {
    card: "#1e1f23",
    ring: "#34363c",
    text: "#ececee",
    muted: "#9a9da6",
    line: "#6c6f78",
    lifeline: "#2c2e33",
    note: "#2a2416",
    noteRing: "#5e4a1e",
    activation: "#2a2340",
    activationRing: "#6d52c4",
    shadow: "drop-shadow(0 1px 2px rgba(0, 0, 0, 0.45))",
  },
};

type Palette = (typeof palettes)["light"];

const shapes = ":is(rect, path, polygon, circle, ellipse)";

// A category hue mixed into the card, text, and line colors, so it reads in both themes.
function shades(hue: string, p: Palette) {
  return {
    fill: `color-mix(in srgb, ${hue} 9%, ${p.card})`,
    ring: `color-mix(in srgb, ${hue} 45%, ${p.card})`,
    label: `color-mix(in srgb, ${hue} 72%, ${p.text})`,
    line: `color-mix(in srgb, ${hue} 70%, ${p.line})`,
    lifeline: `color-mix(in srgb, ${hue} 35%, ${p.card})`,
  };
}

// Scoped to the diagram by Mermaid. Nodes, states, and actors are cards on the panel; groups and frames are hairline outlines.
function themeCSS(p: Palette) {
  return [
    `.node ${shapes}, .statediagram-state ${shapes}, rect.actor { fill: ${p.card}; stroke: ${p.ring}; stroke-width: 1px; filter: ${p.shadow}; }`,
    `.node rect, rect.actor { rx: 10px; ry: 10px; }`,
    `.node .state-start { fill: ${p.text}; stroke: none; filter: none; }`,
    `.nodeLabel, .nodeLabel p { color: ${p.text}; font-weight: 500; text-wrap: balance; }`,
    `text.actor > tspan { fill: ${p.text}; font-weight: 500; }`,
    `.messageText, .noteText { fill: ${p.text}; }`,
    `.flowchart-link, .transition { stroke: ${p.line}; stroke-width: 1.25px; }`,
    `.messageLine0, .messageLine1 { stroke: ${p.line}; stroke-width: 1.25px; }`,
    `.actor-line { stroke: ${p.lifeline}; stroke-width: 1px; stroke-dasharray: 4 4; }`,
    `.step { fill: ${p.text}; }`,
    `.step-number { fill: ${p.card}; font-size: 10px; font-weight: 700; }`,
    `rect.note { fill: ${p.note}; stroke: ${p.noteRing}; rx: 8px; ry: 8px; }`,
    `.frame { fill: ${p.card}; fill-opacity: 0.55; stroke: ${p.ring}; stroke-width: 1px; }`,
    `.frame-tag { fill: color-mix(in srgb, ${p.text} 8%, ${p.card}); }`,
    `.loopLine { stroke: ${p.ring}; stroke-width: 1px; }`,
    // Mermaid sets these text sizes inline.
    `.labelText { fill: ${p.text}; font-size: 10px !important; font-weight: 700 !important; letter-spacing: 0.06em; }`,
    `.loopText, .loopText tspan, .sectionTitle { fill: ${p.muted}; font-size: 12px !important; font-weight: 500 !important; }`,
    `.cluster rect { fill: none !important; stroke: ${p.ring} !important; rx: 14px; ry: 14px; }`,
    `.cluster-label .nodeLabel, .cluster-label .nodeLabel p { font-size: 12px; font-weight: 600; color: ${p.muted}; }`,
    // Edge labels mask the line beneath with the panel color, laid over the page once because the panel is translucent.
    `.edgeLabel, .edgeLabel p { background-color: transparent !important; font-size: 12px; font-weight: 500; color: ${p.muted}; text-wrap: balance; }`,
    `.edgeLabel foreignObject { overflow: visible; }`,
    `.labelBkg { position: relative; left: -6px; top: -1px; padding: 1px 6px; border-radius: 6px; background: linear-gradient(var(--a13n-surface), var(--a13n-surface)), var(--background) !important; }`,
    ...Object.entries(categories).flatMap(([name, hue]) => {
      const c = shades(hue, p);
      return [
        `.${name} ${shapes} { fill: ${c.fill} !important; stroke: ${c.ring} !important; }`,
        `.${name} :is(.nodeLabel, .nodeLabel p) { color: ${c.label}; }`,
        `.${name} text.actor > tspan { fill: ${c.label}; }`,
        `.actor-line.${name} { stroke: ${c.lifeline}; }`,
        `.messageLine0.${name}, .messageLine1.${name} { stroke: ${c.line}; }`,
        `.step.${name} { fill: ${hue}; }`,
      ];
    }),
  ].join("\n");
}

function svgElement(
  doc: Document,
  tag: string,
  attributes: Record<string, string | number>,
) {
  const element = doc.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attributes))
    element.setAttribute(name, String(value));
  return element;
}

// Rewraps a message into the same number of lines, with lengths as even as possible.
function balance(lines: Element[]) {
  const words = lines
    .map((line) => line.textContent)
    .join(" ")
    .split(" ");
  const wrap = (width: number) =>
    words.reduce<string[]>((rows, word) => {
      const last = rows.at(-1);
      if (last && last.length + 1 + word.length <= width)
        rows[rows.length - 1] = `${last} ${word}`;
      else rows.push(word);
      return rows;
    }, []);
  let width = Math.max(...words.map((word) => word.length));
  while (wrap(width).length > lines.length) width++;
  const rows = wrap(width);
  lines.forEach((line, index) => (line.textContent = rows[index] ?? ""));
}

/*
 * Participants take the categories from their source comments, and so do their
 * lifelines, their messages, and the numbered step at the start of each
 * message. Frames such as alt and loop become panels with a label tag.
 */
function refineSequence(doc: Document, chart: string, p: Palette) {
  const owners = new Map<string, string>();
  for (const [, ids, name] of chart.matchAll(classComment))
    if (categories[name]) for (const id of ids.split(",")) owners.set(id, name);
  const ownerOf = (element: Element) =>
    owners.get(
      element.getAttribute("data-from") ??
        element.getAttribute("data-id") ??
        "",
    );

  for (const element of doc.querySelectorAll(
    "[data-et=participant], [data-et=life-line], [data-et=message]",
  )) {
    const name = ownerOf(element);
    if (!name) continue;
    element.classList.add(name);
    // Markers do not take the color of their line, so each category gets its own copy of the arrowhead.
    for (const attribute of ["marker-start", "marker-end"]) {
      const id = /#([^)]+)/.exec(element.getAttribute(attribute) ?? "")?.[1];
      const marker = id && doc.getElementById(id);
      if (!marker) continue;
      if (!doc.getElementById(`${id}-${name}`)) {
        const copy = marker.cloneNode(true) as Element;
        const color = shades(categories[name], p).line;
        copy.id = `${id}-${name}`;
        for (const shape of copy.children)
          shape.setAttribute("style", `fill: ${color}; stroke: ${color}`);
        marker.after(copy);
      }
      element.setAttribute(attribute, `url(#${id}-${name})`);
    }
  }

  // Mermaid spaces the lines of a wrapped participant name by the font size alone.
  for (const line of doc.querySelectorAll("text.actor > tspan")) {
    const dy = Number(line.getAttribute("dy"));
    if (dy) line.setAttribute("dy", String(dy * 1.3));
  }

  doc.querySelectorAll("[data-et=message]").forEach((message, index) => {
    const lines: Element[] = [];
    for (
      let text = message.previousElementSibling;
      text?.classList.contains("messageText");
      text = text.previousElementSibling
    )
      lines.unshift(text);
    if (lines.length > 1) balance(lines);
    const [x, y] =
      message.tagName === "line"
        ? [message.getAttribute("x1"), message.getAttribute("y1")]
        : (/M\s*([\d.-]+)[\s,]+([\d.-]+)/
            .exec(message.getAttribute("d") ?? "")
            ?.slice(1) ?? []);
    if (!x || !y) return;
    const name = ownerOf(message);
    const number = svgElement(doc, "text", {
      x,
      y,
      class: "step-number",
      "text-anchor": "middle",
      "dominant-baseline": "central",
    });
    number.textContent = String(index + 1);
    message.after(
      svgElement(doc, "circle", {
        cx: x,
        cy: y,
        r: 8,
        class: name ? `step ${name}` : "step",
      }),
      number,
    );
  });

  const svg = doc.documentElement;
  for (const frame of doc.querySelectorAll("[data-et=control-structure]")) {
    const lines = [...frame.querySelectorAll(":scope > line.loopLine")];
    if (!lines.length) continue;
    const at = (line: Element, name: string) => Number(line.getAttribute(name));
    const xs = lines.flatMap((line) => [at(line, "x1"), at(line, "x2")]);
    const ys = lines.flatMap((line) => [at(line, "y1"), at(line, "y2")]);
    const [left, right] = [Math.min(...xs), Math.max(...xs)];
    const [top, bottom] = [Math.min(...ys), Math.max(...ys)];
    // A rounded panel under the lifelines replaces the square outline; dashed section separators stay.
    for (const line of lines) {
      const vertical = at(line, "x1") === at(line, "x2");
      const edge = vertical ? [left, right] : [top, bottom];
      if (edge.includes(at(line, vertical ? "x1" : "y1"))) line.remove();
    }
    svg.insertBefore(
      svgElement(doc, "rect", {
        x: left,
        y: top,
        width: right - left,
        height: bottom - top,
        rx: 10,
        class: "frame",
      }),
      svg.firstChild,
    );
    frame.querySelector(":scope > .labelBox")?.remove();
    const label = frame.querySelector(":scope > .labelText");
    if (!label) continue;
    label.textContent = (label.textContent ?? "").toUpperCase();
    const width = label.textContent.length * 7.5 + 14;
    label.before(
      svgElement(doc, "rect", {
        x: left + 8,
        y: top + 8,
        width,
        height: 18,
        rx: 9,
        class: "frame-tag",
      }),
    );
    label.setAttribute("x", String(left + 8 + width / 2));
    label.setAttribute("y", String(top + 17));
  }
}

// Mermaid centers subgraph titles, where edges entering from above cross them, and sizes arrowheads per diagram type.
function refine(svg: string, chart: string, p: Palette) {
  const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  for (const cluster of doc.querySelectorAll(".cluster")) {
    const rect = cluster.querySelector(":scope > rect");
    const label = cluster.querySelector(":scope > .cluster-label");
    const y = /,\s*([\d.-]+)\)/.exec(
      label?.getAttribute("transform") ?? "",
    )?.[1];
    if (rect && label && y)
      label.setAttribute(
        "transform",
        `translate(${Number(rect.getAttribute("x")) + 14}, ${Number(y) + 2})`,
      );
  }
  for (const marker of doc.querySelectorAll("marker")) {
    const width = Number(marker.getAttribute("markerWidth"));
    const height = Number(marker.getAttribute("markerHeight"));
    if (!height || !(width > 10)) continue;
    // A viewBox makes the marker's content scale with its size instead of being clipped.
    if (!marker.hasAttribute("viewBox"))
      marker.setAttribute("viewBox", `0 0 ${width} ${height}`);
    marker.setAttribute("markerWidth", String(width * ARROW_SCALE));
    marker.setAttribute("markerHeight", String(height * ARROW_SCALE));
  }
  if (doc.documentElement.getAttribute("aria-roledescription") === "sequence")
    refineSequence(doc, chart, p);
  return new XMLSerializer().serializeToString(doc);
}

/** Renders a Mermaid diagram with the site palette in both themes. */
export function Mermaid({ chart }: { chart: string }) {
  const id = useId().replaceAll(":", "");
  const { resolvedTheme } = useTheme();
  const [diagram, setDiagram] = useState({ svg: "", minWidth: 0 });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const { default: mermaid } = await import("mermaid");
      const p = resolvedTheme === "dark" ? palettes.dark : palettes.light;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        // The resolved font stack, so Mermaid measures labels with the font that renders them.
        fontFamily: getComputedStyle(document.documentElement)
          .getPropertyValue("--a13n-font")
          .trim(),
        // Sequence diagrams take their actor, message, and note size from this value.
        fontSize: 14,
        // Compact spacing keeps typical diagrams within the reading column.
        flowchart: {
          nodeSpacing: 32,
          rankSpacing: 48,
          padding: 12,
          wrappingWidth: 200,
        },
        sequence: {
          width: 160,
          height: 44,
          actorMargin: 40,
          messageMargin: 32,
          mirrorActors: false,
          wrap: true,
        },
        // The classic look leaves styling to the rules above; the default adds gradients and gray shadows.
        look: "classic",
        theme: "base",
        themeCSS: themeCSS(p),
        themeVariables: {
          fontSize: "14px",
          background: "transparent",
          primaryColor: p.card,
          primaryBorderColor: p.ring,
          primaryTextColor: p.text,
          lineColor: p.line,
          secondaryColor: p.card,
          tertiaryColor: p.card,
          clusterBkg: p.card,
          clusterBorder: p.ring,
          actorBkg: p.card,
          actorBorder: p.ring,
          actorTextColor: p.text,
          signalColor: p.line,
          signalTextColor: p.text,
          noteBkgColor: p.note,
          noteBorderColor: p.noteRing,
          noteTextColor: p.text,
          activationBkgColor: p.activation,
          activationBorderColor: p.activationRing,
        },
      });
      const { svg } = await mermaid.render(`mermaid-${id}`, chart);
      const width = Number(
        /viewBox="[\d.-]+ [\d.-]+ ([\d.]+)/.exec(svg)?.[1] ?? 0,
      );
      if (!cancelled)
        setDiagram({
          svg: refine(svg, chart, p),
          minWidth: Math.round(width * MIN_SCALE),
        });
    })();
    return () => {
      cancelled = true;
    };
  }, [chart, id, resolvedTheme]);

  return (
    <div
      className="not-prose relative my-6 overflow-x-auto rounded-xl border border-(--a13n-border) bg-(--a13n-surface) px-4 py-6 sm:px-6 sm:py-8"
      aria-label="Diagram"
      role="img"
    >
      {/* A dot grid that fades toward the edges, so the diagram reads as placed on a canvas. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(var(--a13n-input-border)_1px,transparent_1px)] bg-size-[16px_16px] mask-[radial-gradient(ellipse_at_center,black_30%,transparent_85%)]"
      />
      <div
        style={{ minWidth: diagram.minWidth }}
        className="relative flex justify-center [&_svg]:h-auto [&_svg]:max-w-full"
        dangerouslySetInnerHTML={{ __html: diagram.svg }}
      />
    </div>
  );
}
