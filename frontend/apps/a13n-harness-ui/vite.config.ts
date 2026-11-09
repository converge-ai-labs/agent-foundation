import tailwindcss from "@tailwindcss/vite";
import { cossLicense } from "a13n-ui/vite";
import { defineConfig } from "vite";
import { novncLicense } from "./novnc-license";

import { pdfResources } from "./pdf-resources";

export default defineConfig({
  worker: { format: "es" },
  plugins: [tailwindcss(), cossLicense(), novncLicense(), pdfResources()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.A13N_HARNESS_UI_URL ?? "http://127.0.0.1:8765",
        changeOrigin: false,
        ws: true,
      },
    },
  },
  build: {
    license: { fileName: "third-party-licenses.txt" },
    outDir: "dist",
    emptyOutDir: true,
  },
});
