import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, ".", "");
  const apiTarget =
    environment.AGENT_UI_API_PROXY_TARGET ?? "http://127.0.0.1:8765";

  return {
    server: {
      host: "127.0.0.1",
      port: 5174,
      strictPort: true,
      proxy: {
        "^/api(?:[/?]|$)": {
          target: apiTarget,
          changeOrigin: false,
        },
      },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
  };
});
