// Mermaid owns its parser/layout. Load it only when a complete diagram is shown.
// Its global configuration/render state must not race across messages or themes.
let queue: Promise<unknown> = Promise.resolve();
let serial = 0;
export function renderDiagram(source: string, dark: boolean): Promise<string> {
  const render = queue.then(async () => {
    // Diagram-authored configuration and image assets would bypass the workbench's
    // no-remote-media policy. Keep their exact source available instead.
    if (/^\s*---|%%\s*\{|\b(?:img|image)\s*:/m.test(source))
      throw new Error(
        "Diagram configuration and image assets are not supported.",
      );
    const { default: mermaid } = await import("mermaid");
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      suppressErrorRendering: true,
      maxTextSize: 50000,
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
    const { svg } = await mermaid.render(`diagram-${++serial}`, source);
    // An image document cannot execute diagram callbacks or inject styles into
    // the application. Do not bind Mermaid's optional interactive callbacks.
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  });
  queue = render.catch(() => {});
  return render;
}
