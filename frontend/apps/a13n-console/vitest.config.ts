import { defineConfig } from "vitest/config";
export default defineConfig({
  test: {
    deps: {
      optimizer: {
        client: {
          enabled: true,
          // The design-system barrel reaches these external UI module graphs in every isolated test file.
          include: [
            "@base-ui/react/**",
            "@daypicker/react",
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
