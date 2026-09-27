import tailwindcss from "@tailwindcss/vite";
import { cossLicense } from "a13n-ui/vite";
import { defineConfig } from "vite";

const serviceUrl =
  process.env.A13N_CONSOLE_SERVICE_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [tailwindcss(), cossLicense()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: serviceUrl,
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
