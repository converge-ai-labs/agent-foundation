"use client";
import { useTheme } from "next-themes";
import { useEffect, useId, useState } from "react";

// Wide diagrams shrink to fit, but not below this share of their natural size; beyond it they scroll.
const MIN_SCALE = 0.75;

/** Renders a Mermaid diagram with the site's neutral palette in both themes. */
export function Mermaid({ chart }: { chart: string }) {
  const id = useId().replaceAll(":", "");
  const { resolvedTheme } = useTheme();
  const [diagram, setDiagram] = useState({ svg: "", minWidth: 0 });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const { default: mermaid } = await import("mermaid");
      const dark = resolvedTheme === "dark";
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        fontFamily: "var(--a13n-font)",
        // Compact spacing keeps typical diagrams within the reading column.
        flowchart: { nodeSpacing: 32, rankSpacing: 40, padding: 12 },
        sequence: {
          width: 130,
          actorMargin: 32,
          messageMargin: 32,
          mirrorActors: false,
          wrap: true,
        },
        theme: "base",
        themeVariables: {
          fontSize: "14px",
          background: "transparent",
          dropShadow: "none",
          ...(dark
            ? {
                primaryColor: "#262626",
                primaryBorderColor: "#383838",
                primaryTextColor: "#f5f5f5",
                lineColor: "#737373",
                secondaryColor: "#1f1f1f",
                tertiaryColor: "#1f1f1f",
                clusterBkg: "#1c1c1c",
                clusterBorder: "#383838",
                noteBkgColor: "#1f1f1f",
                noteBorderColor: "#383838",
                noteTextColor: "#d4d4d4",
              }
            : {
                primaryColor: "#ffffff",
                primaryBorderColor: "#e5e5e5",
                primaryTextColor: "#262626",
                lineColor: "#a3a3a3",
                secondaryColor: "#fafafa",
                tertiaryColor: "#fafafa",
                clusterBkg: "#fafafa",
                clusterBorder: "#e5e5e5",
                noteBkgColor: "#fafafa",
                noteBorderColor: "#e5e5e5",
                noteTextColor: "#525252",
              }),
        },
      });
      const { svg } = await mermaid.render(`mermaid-${id}`, chart);
      const width = Number(
        /viewBox="[\d.-]+ [\d.-]+ ([\d.]+)/.exec(svg)?.[1] ?? 0,
      );
      if (!cancelled)
        setDiagram({ svg, minWidth: Math.round(width * MIN_SCALE) });
    })();
    return () => {
      cancelled = true;
    };
  }, [chart, id, resolvedTheme]);

  return (
    <div
      className="not-prose my-6 overflow-x-auto rounded-xl bg-(--a13n-surface) p-6"
      aria-label="Diagram"
      role="img"
    >
      <div
        style={{ minWidth: diagram.minWidth }}
        className="flex justify-center [&_:is(.node_rect,rect.actor)]:[rx:6px] [&_svg]:h-auto [&_svg]:max-w-full"
        dangerouslySetInnerHTML={{ __html: diagram.svg }}
      />
    </div>
  );
}
