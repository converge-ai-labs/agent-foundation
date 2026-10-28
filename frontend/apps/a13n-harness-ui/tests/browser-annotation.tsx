import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import type { EditorView } from "@codemirror/view";
import "a13n-ui/styles.css";
import Konva from "konva";
import { exportAnnotation } from "../src/browser/annotation-export";
import type { Bounds } from "../src/browser/annotation-model";
import AnnotationEditor from "../src/browser/annotation-editor";
import { ComposerEditor } from "../src/conversations/composer-editor";
import { ThreadDraft } from "../src/conversations/draft";
import type { Transport } from "../src/transport/client";

// Real canvas and composer, with an upload sink instead of a running backend.
const source = document.createElement("canvas");
source.width = 1600;
source.height = 800;
const ctx = source.getContext("2d")!;
ctx.fillStyle = "#f8fafc";
ctx.fillRect(0, 0, 1600, 800);
ctx.fillStyle = "#0f172a";
ctx.fillRect(0, 0, 1600, 80);
ctx.fillStyle = "#ffffff";
ctx.font = "24px sans-serif";
ctx.fillText("Browser screenshot · local annotation", 32, 49);
ctx.fillStyle = "#2563eb";
ctx.fillRect(100, 180, 380, 220);
ctx.fillStyle = "#16a34a";
ctx.fillRect(600, 180, 380, 220);
ctx.fillStyle = "#dc2626";
ctx.fillRect(1100, 180, 380, 220);
ctx.fillStyle = "#334155";
ctx.font = "32px sans-serif";
ctx.fillText("Private: example@example.test", 100, 520);
ctx.fillText("Draw, crop, undo, copy, then paste below.", 100, 620);
Object.assign(window, {
  annotationFixture: {
    stage: () => Konva.stages[0],
    export: async (crop: Bounds) => {
      const group = Konva.stages[0]
        .getLayers()[0]
        .getChildren()[0] as Konva.Group;
      const blob = await exportAnnotation(group, crop);
      const image = await createImageBitmap(blob);
      const canvas = document.createElement("canvas");
      canvas.width = image.width;
      canvas.height = image.height;
      canvas.getContext("2d")!.drawImage(image, 0, 0);
      image.close();
      return {
        width: canvas.width,
        height: canvas.height,
        png: canvas.toDataURL(),
      };
    },
  },
});
const ready = Promise.resolve();
const draft = new ThreadDraft();
draft.draftId = "annotation-fixture";
function Fixture() {
  const [open, setOpen] = useState(true);
  const [received, setReceived] = useState("");
  const editor = useRef<EditorView | null>(null);
  return (
    <main style={{ padding: 24 }}>
      <button onClick={() => setOpen(true)}>Open editor</button>
      <button onClick={() => document.documentElement.classList.toggle("dark")}>
        Toggle theme
      </button>
      <ComposerEditor
        draft={draft}
        profile={{ display_name: "Tester", color: "#2563eb" }}
        presence={() => {}}
        submit={() => {}}
        editor={editor}
        attachments={{
          transport: {
            fetch: () => Promise.reject(new Error("No backend")),
          } as unknown as Transport,
          threadId: "annotation-fixture",
          metadata: new Map(),
          preview: () => {},
          retry: () => {},
          upload: async (files) => {
            const file = files[0];
            const image = await createImageBitmap(file);
            const canvas = document.createElement("canvas");
            canvas.width = image.width;
            canvas.height = image.height;
            canvas.getContext("2d")!.drawImage(image, 0, 0);
            setReceived(
              JSON.stringify({
                type: file.type,
                size: file.size,
                width: image.width,
                height: image.height,
              }),
            );
            document.getElementById("pasted")!.replaceChildren(canvas);
            image.close();
          },
        }}
      />
      <output id="received">{received}</output>
      <div id="pasted" />
      {open && (
        <AnnotationEditor
          source={source}
          clipboardReady={ready}
          onClose={() => setOpen(false)}
          onCopied={() => {
            setOpen(false);
            requestAnimationFrame(() => editor.current?.focus());
          }}
        />
      )}
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
