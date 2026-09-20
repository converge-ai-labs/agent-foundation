import { defineConfig } from "vitest/config";
export default defineConfig({
  test: {
    // Threads share one process; forks re-load every dependency per worker.
    pool: "threads",
    deps: {
      optimizer: {
        client: {
          enabled: true,
          // The design-system barrel reaches these external UI module graphs in every isolated test file.
          include: [
            "@base-ui/react/**",
            "@daypicker/react",
            "@daypicker/react/locale",
            "@phosphor-icons/react",
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
