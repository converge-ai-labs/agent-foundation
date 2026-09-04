import "./style.css";

const root = document.querySelector<HTMLElement>("#app");

if (root === null) {
  throw new Error("Harness UI root element is missing");
}

root.innerHTML = `
  <section class="shell" aria-labelledby="page-title">
    <p class="eyebrow">Agent Foundation UI</p>
    <h1 id="page-title">Local agent interaction, one shared stream.</h1>
    <p class="summary">
      This private WebUI is bundled into the <code>a13n-ui</code>
      Python distribution. WebUI, TUI, and CLI share one stable AgentUiApp
      and Agent Stream Protocol projection.
    </p>
  </section>
`;
