import { configDefaults, defineConfig } from "vitest/config";

// Keep the Python App protocol suites and their fixture tests off the jsdom runners in CI.
const protocolTests = [
  "**/protocol.test.ts",
  "**/*-protocol.test.ts",
  "tests/app-fixture.test.ts",
];

export default defineConfig({
  test: {
    setupFiles: ["./tests/setup.ts"],
    // Threads share one process; forks re-load every dependency per worker.
    pool: "threads",
    // Bound concurrent jsdom initialization so focus/readiness checks retain
    // their normal deadlines.
    maxWorkers: 4,
    projects: [
      {
        extends: true,
        test: {
          name: "unit",
          exclude: [...configDefaults.exclude, ...protocolTests],
        },
      },
      {
        extends: true,
        test: { name: "protocol", include: protocolTests },
      },
    ],
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
            "@codemirror/language",
            "@codemirror/search",
            "@lezer/highlight",
            "y-codemirror.next",
            "yjs",
            "y-protocols/awareness",
          ],
        },
      },
    },
  },
});
