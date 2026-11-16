import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
const root = fileURLToPath(new URL(".", import.meta.url));
export default {
  root,
  base: "./",
  // A single external module owns React, JSX, query, router and all host contexts.
  oxc: {
    jsx: {
      runtime: "classic",
      pragma: "React.createElement",
      pragmaFrag: "React.Fragment",
    },
  },
  plugins: [
    {
      name: "font-license",
      generateBundle() {
        this.emitFile({
          type: "asset",
          fileName: "SpaceGrotesk-OFL.txt",
          source: readFileSync(`${root}/src/SpaceGrotesk-OFL.txt`, "utf8"),
        });
      },
    },
  ],
  build: {
    outDir: fileURLToPath(
      new URL("../src/acme_reports/static", import.meta.url),
    ),
    emptyOutDir: true,
    assetsInlineLimit: 0,
    cssCodeSplit: false,
    rolldownOptions: {
      input: `${root}/src/index.tsx`,
      preserveEntrySignatures: "strict",
      external: ["@a13n/console"],
      output: {
        entryFileNames: "index.js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames: (asset) =>
          asset.names?.some((name) => name.endsWith(".css"))
            ? "index.css"
            : "assets/[name]-[hash][extname]",
      },
    },
  },
};
