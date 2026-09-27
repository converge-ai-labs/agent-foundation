import { defineConfig } from "vitest/config";
export default defineConfig({
  test: {
    deps: {
      optimizer: {
        client: {
          enabled: true,
          // The package barrel loads these external UI module graphs in every isolated test file.
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
          include: ["tests/**/*.test.ts"],
          environment: "node",
        },
      },
      {
        extends: true,
        test: {
          name: "dom",
          include: ["tests/**/*.test.tsx"],
          environment: "jsdom",
          setupFiles: ["./tests/setup.ts"],
        },
      },
    ],
  },
});
