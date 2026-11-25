import tailwindcss from "@tailwindcss/vite";
import { cossLicense, mermaidFrame } from "a13n-ui/vite";
import { defineConfig } from "vite";
import { resolve } from "node:path";

const serviceUrl =
  process.env.A13N_CONSOLE_SERVICE_URL ?? "http://127.0.0.1:8000";
const bridgeEntry = resolve(import.meta.dirname, "src/app-plugins/bridge.ts");

export default defineConfig(({ command }) => ({
  plugins: [
    tailwindcss(),
    cossLicense(),
    mermaidFrame(),
    {
      name: "app-plugin-bridge",
      transformIndexHtml: {
        order: "post",
        handler: (_html, context) => {
          const bridge = Object.values(context.bundle ?? {}).find(
            (output) =>
              output.type === "chunk" && output.facadeModuleId === bridgeEntry,
          );
          if (command === "build" && !bridge)
            throw new Error("Console build did not emit its App bridge.");
          return [
            {
              tag: "script",
              attrs: { type: "importmap" },
              injectTo: "head-prepend",
              children: JSON.stringify({
                imports: {
                  "@a13n/console":
                    command === "serve"
                      ? "/src/app-plugins/bridge.ts"
                      : `/${bridge!.fileName}`,
                },
              }),
            },
          ];
        },
      },
    },
  ],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: serviceUrl,
        changeOrigin: false,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rolldownOptions: {
      input: {
        index: resolve(import.meta.dirname, "index.html"),
        "app-bridge-v1": bridgeEntry,
      },
      preserveEntrySignatures: "strict",
      output: { entryFileNames: "assets/[name]-[hash].js" },
    },
  },
}));
