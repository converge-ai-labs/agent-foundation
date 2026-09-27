import { build } from "esbuild";
import { mkdir, readFile, writeFile } from "node:fs/promises";

const result = await build({
  entryPoints: ["app.js"],
  bundle: true,
  format: "iife",
  target: "es2022",
  minify: true,
  write: false,
});
const template = await readFile("app.html", "utf8");
// One self-contained MCP resource: no CDN, development server or network grant.
const script = result.outputFiles[0].text.replaceAll("</script", "<\\/script");
await mkdir("src/mcp_apps_example/assets", { recursive: true });
await writeFile(
  "src/mcp_apps_example/assets/app.html",
  template.replace("<!-- APP_SCRIPT -->", () => `<script>${script}</script>`),
);
