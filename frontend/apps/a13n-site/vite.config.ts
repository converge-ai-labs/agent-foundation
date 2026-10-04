import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Cloudflare Pages serves the root 404.html for unknown paths.
    rolldownOptions: { input: ["index.html", "404.html"] },
  },
});
