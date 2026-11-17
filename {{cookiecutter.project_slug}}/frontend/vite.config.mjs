import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));
export default {
  root,
  base: "./",
  oxc: {
    jsx: {
      runtime: "classic",
      pragma: "React.createElement",
      pragmaFrag: "React.Fragment",
    },
  },
  build: {
    outDir: fileURLToPath(new URL("../src/{{ cookiecutter.module_name }}/static", import.meta.url)),
    emptyOutDir: true,
    cssCodeSplit: false,
    rolldownOptions: {
      input: `${root}/src/index.tsx`,
      preserveEntrySignatures: "strict",
      external: ["@a13n/console"],
      output: {
        entryFileNames: "index.js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames: (asset) => asset.names?.some((name) => name.endsWith(".css"))
          ? "index.css" : "assets/[name]-[hash][extname]",
      },
    },
  },
};
