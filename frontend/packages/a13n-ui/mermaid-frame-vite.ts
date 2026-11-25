import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { build, normalizePath, type Plugin } from "vite";

const fileName = "mermaid-sandbox.html";
const urlModule = normalizePath(
  fileURLToPath(new URL("./src/mermaid-frame-url.ts", import.meta.url)),
);
const template = new URL(`./src/${fileName}`, import.meta.url);
const entry = fileURLToPath(new URL("./src/mermaid-frame.ts", import.meta.url));

async function buildFrame() {
  // Bundle all diagram types into one classic script. An opaque document can
  // run this hash-pinned script without loading any scripts or chunks over HTTP.
  const output = await build({
    configFile: false,
    publicDir: false,
    logLevel: "warn",
    build: {
      write: false,
      license: { fileName: "mermaid-licenses.txt" },
      lib: { entry, formats: ["iife"], name: "MermaidFrame" },
    },
  });
  const result = Array.isArray(output) ? output[0] : output;
  if (!result || !("output" in result))
    throw new Error("Expected one Mermaid frame build");
  const scripts = result.output.filter((item) => item.type === "chunk");
  const licenses = result.output.find(
    (item) => item.type === "asset" && item.fileName === "mermaid-licenses.txt",
  );
  if (scripts.length !== 1 || licenses?.type !== "asset")
    throw new Error("Expected one Mermaid frame script and its licenses");
  // Hash the bytes that the HTML parser will see.
  const code = scripts[0].code
    .replace(/\r\n?/g, "\n")
    .replace(/<\/script/gi, "<\\/script")
    .replace(/<!--/g, "<\\x21--");
  const hash = createHash("sha256").update(code).digest("base64");
  const html = readFileSync(template, "utf8")
    .replace("__MERMAID_HASH__", hash)
    .replace("<!-- __MERMAID_SCRIPT__ -->", () => `<script>${code}</script>`);
  return { html, licenses };
}

export function mermaidFrame(): Plugin {
  let devUrl: string | undefined;
  return {
    name: "a13n-ui-mermaid-frame",
    configureServer(server) {
      devUrl = `${server.config.base}assets/${fileName}`;
      let frame: ReturnType<typeof buildFrame> | undefined;
      const files = [entry, fileURLToPath(template)];
      server.watcher.add(files);
      server.watcher.on("change", (path) => {
        if (files.includes(path)) frame = undefined;
      });
      server.middlewares.use(async (req, res, next) => {
        if (req.url !== devUrl) return next();
        try {
          const { html } = await (frame ??= buildFrame());
          res.setHeader("Content-Type", "text/html; charset=utf-8");
          res.setHeader("Cache-Control", "no-store");
          res.end(html);
        } catch (error) {
          frame = undefined;
          next(error);
        }
      });
    },
    async transform(_code, id) {
      if (normalizePath(id) !== urlModule) return;
      if (devUrl) return `export default ${JSON.stringify(devUrl)};`;
      const { html, licenses } = await buildFrame();
      this.addWatchFile(entry);
      this.addWatchFile(fileURLToPath(template));
      this.emitFile({
        type: "asset",
        fileName: `assets/${licenses.fileName}`,
        source: licenses.source,
      });
      const ref = this.emitFile({
        type: "asset",
        name: fileName,
        source: html,
      });
      return `export default import.meta.ROLLUP_FILE_URL_${ref};`;
    },
  };
}
