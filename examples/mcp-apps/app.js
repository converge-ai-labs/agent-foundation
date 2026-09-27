import { App, applyDocumentTheme } from "@modelcontextprotocol/ext-apps";

const app = new App({ name: "Session counter", version: "1.0.0" });
const count = document.getElementById("count");
const status = document.getElementById("status");
let current = 0;

function show(result) {
  if (result.isError) throw new Error("The server rejected the operation.");
  const value = result.structuredContent?.count;
  if (typeof value !== "number") throw new Error("The server returned no counter value.");
  current = value;
  count.textContent = String(value);
}
function theme(context) {
  if (context.theme) applyDocumentTheme(context.theme);
}
// Handlers must be ready before the Host sends the original tool result.
app.ontoolresult = (result) => {
  show(result);
  status.textContent = "Showing the original result. Activate interactions in the Host card to use these controls.";
};
app.onhostcontextchanged = theme;
app.onerror = (error) => { status.textContent = error.message; };
app.onteardown = async () => ({});

function action(id, run) {
  const button = document.getElementById(id);
  button.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "Waiting for the Host. Any required confirmation is outside this App.";
    try {
      status.textContent = await run();
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : String(error);
    } finally {
      button.disabled = false;
    }
  });
}
action("increment", async () => {
  show(await app.callServerTool({ name: "increment_counter", arguments: {} }));
  return "Updated on the existing MCP session; no conversational Run was started.";
});
action("reset", async () => {
  show(await app.callServerTool({ name: "reset_counter", arguments: {} }));
  return "Counter reset after Host authorization.";
});
action("read", async () => {
  const result = await app.readServerResource({ uri: "data://counter/current" });
  const resource = result.contents.find((item) => "text" in item);
  if (!resource) throw new Error("No text resource returned.");
  show({ structuredContent: JSON.parse(resource.text) });
  return "Read the current server resource without invoking a tool.";
});
action("context", async () => {
  await app.updateModelContext({
    content: [{ type: "text", text: `The selected counter value is ${current}.` }],
    structuredContent: { count: current },
  });
  return "Context offered. Select it in the trusted Host card before sending an ordinary message.";
});
action("send", async () => {
  const result = await app.sendMessage({
    role: "user",
    content: [{ type: "text", text: document.getElementById("message").value }],
  });
  return result.isError ? "The Host declined the message." : "The Host accepted the confirmed message.";
});
action("link", async () => {
  const result = await app.openLink({ url: "https://modelcontextprotocol.io/" });
  return result.isError ? "The Host declined the link." : "The Host opened the confirmed link.";
});
app.connect().then(() => {
  theme(app.getHostContext() ?? {});
  for (const button of document.querySelectorAll("button")) button.disabled = false;
}).catch((error) => { status.textContent = error.message; });
