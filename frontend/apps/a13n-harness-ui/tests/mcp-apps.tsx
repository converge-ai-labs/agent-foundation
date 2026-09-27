import { createRoot } from "react-dom/client";
import { AppFrame } from "../src/mcp-apps/app-frame";

const sandboxUrl = new URLSearchParams(location.search).get("sandbox");
if (!sandboxUrl)
  throw new Error("Pass ?sandbox=http://127.0.0.1:PORT/sandbox.html");

const html = `<!doctype html><html lang="en"><body>
<h2>Isolated counter</h2><p id="status">Connecting</p><button id="increment">Increment</button>
<script>
const send = (value) => parent.postMessage({jsonrpc:'2.0',...value}, '*');
const checks = {};
for (const [name, read] of Object.entries({
  hostDOM: () => top.document.body.innerHTML,
  proxyDOM: () => parent.document.body.innerHTML,
  cookie: () => document.cookie,
  storage: () => localStorage.getItem('host-secret'),
})) { try { read(); checks[name] = 'FAILED'; } catch { checks[name] = 'blocked'; } }
window.addEventListener('message', ({source,data}) => {
  if (source !== parent) return;
  if (data.id === 1 && data.result) {
    send({method:'ui/notifications/initialized',params:{}});
    send({id:2,method:'ui/update-model-context',params:{structuredContent:checks}});
    document.querySelector('#status').textContent = 'Ready';
  }
  if (data.method === 'ui/notifications/tool-result') document.querySelector('#status').textContent = 'Initial result: ' + data.params.structuredContent.count;
  if (data.id === 3 && data.result) document.querySelector('#status').textContent = 'Counter: ' + data.result.structuredContent.count;
});
document.querySelector('#increment').onclick = () => send({id:3,method:'tools/call',params:{name:'increment',arguments:{}}});
send({id:1,method:'ui/initialize',params:{appInfo:{name:'isolation-test',version:'1.0.0'},appCapabilities:{},protocolVersion:'2026-01-26'}});
</script></body></html>`;
let count = 0;
createRoot(document.getElementById("root")!).render(
  <main>
    <h1>MCP Apps isolation fixture</h1>
    <pre id="checks">Waiting for View</pre>
    <AppFrame
      title="Isolation test"
      sandboxUrl={sandboxUrl}
      html={html}
      arguments={{}}
      result={{ content: [], structuredContent: { count: 0 } }}
      capabilities={{
        serverTools: {},
        updateModelContext: { structuredContent: true },
      }}
      handlers={{
        oncalltool: async () => ({
          content: [],
          structuredContent: { count: ++count },
        }),
        onupdatemodelcontext: async (params) => {
          document.getElementById("checks")!.textContent = JSON.stringify(
            params.structuredContent,
          );
          return {};
        },
      }}
    />
  </main>,
);
