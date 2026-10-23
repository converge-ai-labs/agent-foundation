import { defineConfig } from "vitest/config";
export default defineConfig({
  // PromQL publishes both formats without exports; use its ESM entry like Vite.
  resolve: {
    alias: {
      "@prometheus-io/codemirror-promql":
        "@prometheus-io/codemirror-promql/dist/esm/index.js",
    },
  },
  test: {
    // Threads share one process; forks re-load every dependency per worker.
    pool: "threads",
    deps: {
      optimizer: {
        client: {
          enabled: true,
          // jsdom runs in Node; lru-cache's Node entry uses the real diagnostics channel.
          exclude: ["node:diagnostics_channel"],
          // The design-system barrel reaches these external UI module graphs in every isolated test file.
          include: [
            "@base-ui/react/**",
            "@daypicker/react",
            "@daypicker/react/locale",
            "@phosphor-icons/react",
            // Keep the editor and PromQL extensions in one graph for class identity.
            "codemirror",
            "@codemirror/state",
            "@codemirror/view",
            "@prometheus-io/codemirror-promql",
          ],
        },
      },
    },
    projects: [
      {
        extends: true,
        test: {
          name: "unit",
          include: ["src/**/*.test.ts", "src/service-client/**/*.test.mjs"],
          environment: "node",
        },
      },
      {
        extends: true,
        test: {
          name: "dom",
          include: ["src/**/*.test.tsx"],
          environment: "jsdom",
          setupFiles: ["./tests/setup.ts"],
        },
      },
    ],
  },
});
