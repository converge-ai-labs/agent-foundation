import { defineConfig } from "vite";
import { blog } from "./blog/plugin.ts";

export default defineConfig({
  plugins: [blog()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Cloudflare Pages serves the root 404.html for unknown paths.
    rolldownOptions: { input: ["index.html", "404.html"] },
  },
});
