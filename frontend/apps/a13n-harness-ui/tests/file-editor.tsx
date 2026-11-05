import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { EditorView } from "@codemirror/view";
import shell from "../src/shell/workbench.module.css";
import native from "../src/native/native.module.css";
import "a13n-ui/styles.css";
import "../src/shell/theme.css";
import { FileView } from "../src/native/file-view";
import { FileBuffers } from "../src/native/buffer";
import { TransportContext } from "../src/transport/context";
import type { Schema, Transport } from "../src/transport/client";

// Production FileView/CodeMirror with a memory-only file endpoint. No Host files.
const params = new URLSearchParams(location.search);
const path = params.get("plain") ? "/review/notes.txt" : "/review/notes.md";
let disk: Schema<"FileText"> = {
  resolved_path: path,
  presentation: "text",
  text: [
    "# Editing notes",
    "",
    "Select this sentence with the mouse, then copy or replace it.",
    "",
    "## Review checklist",
    "",
    "- Keep the selection visible in light and dark themes.",
    "- Preview the current draft without losing undo history.",
    "- Find a word, jump to a line, and continue editing.",
    "",
    ...Array.from(
      { length: 60 },
      (_, i) =>
        `Paragraph ${i + 1}: ${"Long text should wrap without losing the reading position. ".repeat(3)}`,
    ),
    "",
    "[Diagram](/threads/review?native=files&native_path=%2Freview%2Fdiagram.png)",
  ].join("\n"),
  entry: {
    path,
    kind: "file",
    revision: "one",
    size: 12000,
    modified_ns: 0,
    mode: 0,
  },
};
const buffers = new Map();
const transport = {
  client: {
    GET: async () => ({ data: disk }),
    PUT: async (_: string, { body }: { body: { text: string } }) => {
      disk = {
        ...disk,
        text: body.text,
        entry: {
          ...disk.entry,
          revision: `${disk.entry.revision}-saved`,
          size: body.text.length,
        },
      };
      return { data: disk.entry };
    },
  },
} as unknown as Transport;
Object.assign(window, {
  fileEditorFixture: {
    view: () => EditorView.findFromDOM(document.querySelector(".cm-editor")!),
    disk: () => disk.text,
  },
});
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    <TransportContext value={transport}>
      <FileBuffers value={buffers}>
        <main className={shell.shell}>
          <div
            className={native.editorPage}
            role="region"
            aria-label="File reader"
            tabIndex={0}
          >
            <FileView
              path={path}
              refresh={() => {}}
              open={() => {}}
              line={params.has("line") ? Number(params.get("line")) : undefined}
            />
          </div>
        </main>
      </FileBuffers>
    </TransportContext>
  </QueryClientProvider>,
);
