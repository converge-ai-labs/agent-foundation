import mermaid from "mermaid";
import {
  maxTextSize,
  maxEdges,
  type FrameReply,
  type FrameRequest,
} from "./mermaid-protocol";

// The HTML asset is also addressable by URL. Only an opaque-origin frame
// embedded by the app may accept user diagrams.
if (window.origin === "null" && parent !== window) {
  const appOrigin = location.origin;
  const reply = (message: FrameReply) => parent.postMessage(message, appOrigin);
  window.addEventListener(
    "message",
    async (event: MessageEvent<FrameRequest>) => {
      if (
        event.source !== parent ||
        event.origin !== appOrigin ||
        event.data?.kind !== "render-mermaid"
      )
        return;
      const { id, source, dark } = event.data;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        suppressErrorRendering: true,
        maxTextSize,
        maxEdges,
        theme: "base",
        htmlLabels: true,
        flowchart: { curve: "basis", padding: 18 },
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
      const host = document.createElement("div");
      document.body.append(host);
      try {
        // The parent displays the SVG as an image; never bind interactive callbacks.
        const { svg } = await mermaid.render(`diagram-${id}`, source, host);
        // Mermaid returns HTML serialization, where void labels like <br> are
        // legal. Serialize the inert SVG as XML for the parent's image URL.
        const markup = document.createElement("template");
        markup.innerHTML = svg;
        const image = new XMLSerializer().serializeToString(
          markup.content.firstElementChild!,
        );
        reply({ kind: "mermaid-result", id, svg: image });
      } catch (error) {
        const reason =
          error instanceof Error && error.name === "UnknownDiagramError"
            ? "unsupported-type"
            : error instanceof Error &&
                error.message.startsWith("Edge limit exceeded.")
              ? "too-many-edges"
              : error instanceof DOMException && error.name === "EncodingError"
                ? "image-unavailable"
                : "invalid-diagram";
        reply({ kind: "mermaid-result", id, reason });
      } finally {
        host.remove();
      }
    },
  );
  reply({ kind: "mermaid-ready" });
}
