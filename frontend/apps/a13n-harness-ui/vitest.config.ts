import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // CI uses an eight-core runner; leave capacity for the real Python App fixtures.
    maxWorkers: process.env.CI ? 4 : 2,
    deps: {
      optimizer: {
        client: {
          enabled: true,
          // jsdom runs in Node; Yjs must retain the real Node crypto implementation.
          exclude: ["node:crypto"],
          // Bundle external UI barrels once instead of importing their full graphs per file.
          include: [
            "@base-ui/react/**",
            "a13n-ui > @daypicker/react",
            "@phosphor-icons/react",
            // Keep the editor and its CRDT bindings in one graph for class identity.
            "codemirror",
            "@codemirror/state",
            "@codemirror/view",
            "@codemirror/commands",
            "@codemirror/autocomplete",
            "@codemirror/lang-yaml",
            "y-codemirror.next",
            "yjs",
            "y-protocols/awareness",
          ],
        },
      },
    },
  },
});
