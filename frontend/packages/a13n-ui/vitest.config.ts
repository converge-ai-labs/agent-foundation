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
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    setupFiles: ["./tests/setup.ts"],
  },
});
