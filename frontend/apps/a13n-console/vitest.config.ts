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
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
  },
});
