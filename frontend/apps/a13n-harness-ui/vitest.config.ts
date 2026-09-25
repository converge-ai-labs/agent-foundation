import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Local measurements favor threads; preserve the existing CI pool.
    pool: process.env.CI ? "forks" : "threads",
    // CI uses an eight-core runner; leave capacity for the real Python App fixtures.
    // Locally, Vitest's default (one per core, minus one) runs about twice as fast as two.
    maxWorkers: process.env.CI ? 4 : undefined,
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
            "@codemirror/lang-javascript",
            "@codemirror/lang-python",
            "@codemirror/lang-json",
            "@codemirror/lang-markdown",
            "@codemirror/search",
            "y-codemirror.next",
            "yjs",
            "y-protocols/awareness",
          ],
        },
      },
    },
  },
});
