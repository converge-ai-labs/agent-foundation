import { readFileSync, readdirSync } from "node:fs";
import type { Plugin } from "vite";

// PDF.js resolves character maps, fonts and optional decoders by their original
// filenames. Ship these alongside the worker instead of consulting a CDN.
export function pdfResources(): Plugin {
  const root = new URL("../", import.meta.resolve("pdfjs-dist"));
  const { version } = JSON.parse(
    readFileSync(new URL("package.json", root), "utf8"),
  ) as { version: string };
  const resources = new Map<string, URL>();
  for (const directory of ["cmaps", "standard_fonts", "iccs", "wasm"]) {
    const base = new URL(directory + "/", root);
    for (const file of readdirSync(base))
      resources.set(
        `assets/pdfjs-${version}/${directory}/${file}`,
        new URL(file, base),
      );
  }
  return {
    name: "harness-pdf-resources",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const key = req.url?.split("?")[0]?.slice(1) ?? "",
          file = resources.get(key);
        if (!file) return next();
        res.setHeader(
          "Content-Type",
          key.endsWith(".wasm")
            ? "application/wasm"
            : key.endsWith(".js")
              ? "text/javascript"
              : "application/octet-stream",
        );
        res.end(readFileSync(file));
      });
    },
    generateBundle() {
      for (const [fileName, file] of resources)
        this.emitFile({ type: "asset", fileName, source: readFileSync(file) });
    },
  };
}
