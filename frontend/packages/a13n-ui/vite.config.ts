import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
export default defineConfig({
  root: fileURLToPath(new URL("./dev", import.meta.url)),
  server: { host: "127.0.0.1", port: 5175, strictPort: true },
  build: { outDir: "../dist/showcase", emptyOutDir: true },
});
