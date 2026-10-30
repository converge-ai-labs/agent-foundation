import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type Konva from "konva";
import {
  Button,
  Dialog,
  DialogDescription,
  ModalFrame,
  DialogPopup,
  DialogTitle,
} from "a13n-ui";
import {
  ArrowBendUpLeft,
  ArrowBendUpRight,
  ArrowUpRight,
  Circle,
  Clipboard,
  Crop,
  Cursor,
  DownloadSimple,
  PencilSimple,
  Rectangle,
  SelectionAll,
  Square,
  SquaresFour,
  TextT,
  Trash,
  X,
} from "@phosphor-icons/react";
import { AnnotationText } from "./annotation-text";
import { AnnotationCanvas } from "./annotation-canvas";
import {
  copyAnnotation,
  downloadAnnotation,
  exportAnnotation,
} from "./annotation-export";
import {
  clampPoint,
  cropBetween,
  drawAnnotation,
  historyReducer,
  meaningfulAnnotation,
  replaceAnnotation,
  startAnnotation,
  type Annotation,
  type Bounds,
  type Document,
  type Point,
  type Tool,
} from "./annotation-model";
import styles from "./annotation.module.css";

const tools = [
  { tool: "select", label: "Select", key: "v", icon: Cursor },
  { tool: "crop", label: "Crop", key: "c", icon: Crop },
  { tool: "arrow", label: "Arrow", key: "a", icon: ArrowUpRight },
  { tool: "rectangle", label: "Rectangle", key: "r", icon: Rectangle },
  { tool: "ellipse", label: "Ellipse", key: "o", icon: Circle },
  { tool: "pen", label: "Pen", key: "p", icon: PencilSimple },
  { tool: "text", label: "Text", key: "t", icon: TextT },
  { tool: "mosaic", label: "Mosaic", key: "m", icon: SquaresFour },
  { tool: "redact", label: "Opaque redaction", key: "b", icon: Square },
] as const;
const colors = [
  { value: "#ef4444", label: "Red" },
  { value: "#facc15", label: "Yellow" },
  { value: "#22c55e", label: "Green" },
  { value: "#3b82f6", label: "Blue" },
  { value: "#ffffff", label: "White" },
  { value: "#000000", label: "Black" },
];
type TextMark = Extract<Annotation, { kind: "text" }>;

export default function AnnotationEditor({
  source,
  clipboardReady,
  onClose,
  onCopied,
}: {
  source: HTMLCanvasElement;
  clipboardReady: Promise<void>;
  onClose: () => void;
  onCopied: () => void;
}) {
  const bounds = { x: 0, y: 0, width: source.width, height: source.height };
  const [history, dispatch] = useReducer(historyReducer, {
    past: [],
    present: { crop: bounds, annotations: [] },
    future: [],
  });
  const document = history.present;
  const [tool, setTool] = useState<Tool>("arrow");
  const [color, setColor] = useState(colors[0].value);
  const [strokeWidth, setStrokeWidth] = useState(4);
  const [fontSize, setFontSize] = useState(28);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<Annotation | null>(null);
  const [cropDraft, setCropDraft] = useState<Bounds | null>(null);
  const gesture = useRef<{
    start: Point;
    mark: Annotation | null;
    crop: Bounds | null;
  } | null>(null);
  const [text, setText] = useState<TextMark | null>(null);
  const content = useRef<Konva.Group>(null);
  const popup = useRef<HTMLDivElement>(null);
  const copyButton = useRef<HTMLButtonElement>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const [viewportSize, setViewportSize] = useState({ width: 800, height: 500 });
  const [zoom, setZoom] = useState("fit");
  const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const selectedMark = document.annotations.find(
    (mark) => mark.id === selected,
  );
  const scale =
    zoom === "fit"
      ? Math.min(
          1,
          Math.max(1, viewportSize.width) / document.crop.width,
          Math.max(1, viewportSize.height) / document.crop.height,
        )
      : Number(zoom);
  const commit = (next: Document) =>
    dispatch({ type: "commit", document: next });
  useEffect(() => {
    let active = true;
    void clipboardReady.then(() => {
      if (active) setReady(true);
    });
    return () => {
      active = false;
    };
  }, [clipboardReady]);
  useEffect(() => {
    if (ready && window.document.activeElement === popup.current)
      copyButton.current?.focus();
  }, [ready]);
  const requestClose = () => {
    if (saving) return;
    if (text || history.past.length || history.future.length)
      setConfirmClose(true);
    else onClose();
  };
  // The dialog portal mounts after the editor: observe when its viewport attaches.
  const viewport = useCallback((element: HTMLDivElement | null) => {
    if (!element) return;
    const measure = () => {
      const style = getComputedStyle(element);
      setViewportSize({
        width:
          element.clientWidth -
          parseFloat(style.paddingLeft) -
          parseFloat(style.paddingRight),
        height:
          element.clientHeight -
          parseFloat(style.paddingTop) -
          parseFloat(style.paddingBottom),
      });
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    measure();
    return () => observer.disconnect();
  }, []);
  const selectTool = (next: Tool) => {
    setTool(next);
    setSelected(null);
  };
  const change = (mark: Annotation) =>
    commit(replaceAnnotation(document, mark));
  const remove = () => {
    if (!selected) return;
    commit({
      ...document,
      annotations: document.annotations.filter((mark) => mark.id !== selected),
    });
    setSelected(null);
  };
  const finishText = (text: TextMark) => {
    const existing = document.annotations.some((mark) => mark.id === text.id);
    if (meaningfulAnnotation(text))
      commit(
        existing
          ? replaceAnnotation(document, text)
          : { ...document, annotations: [...document.annotations, text] },
      );
    else if (existing)
      commit({
        ...document,
        annotations: document.annotations.filter((mark) => mark.id !== text.id),
      });
    setText(null);
    setSelected(text.id);
    setTool("select");
    popup.current?.focus();
  };
  const cancelGesture = () => {
    gesture.current = null;
    setDraft(null);
    setCropDraft(null);
  };
  const start = (position: Point) => {
    if (saving || text) return;
    const point = clampPoint(position, document.crop);
    setSelected(null);
    if (tool === "text") {
      setText({
        kind: "text",
        id: crypto.randomUUID(),
        ...point,
        scaleX: 1,
        scaleY: 1,
        color,
        strokeWidth,
        fontSize,
        text: "",
      });
    } else if (tool !== "select") {
      const mark =
        tool === "crop"
          ? null
          : startAnnotation(
              tool,
              point,
              { color, strokeWidth, fontSize },
              crypto.randomUUID(),
            );
      gesture.current = { start: point, mark, crop: null };
      setDraft(mark);
    }
  };
  const move = (position: Point, constrained: boolean) => {
    const current = gesture.current;
    if (!current) return;
    const point = clampPoint(position, document.crop);
    if (current.mark) {
      current.mark = drawAnnotation(
        current.mark,
        current.start,
        point,
        constrained,
      );
      setDraft(current.mark);
    } else {
      current.crop = cropBetween(current.start, point, document.crop);
      setCropDraft(current.crop);
    }
  };
  const end = () => {
    const current = gesture.current;
    if (!current) return;
    if (current.mark && meaningfulAnnotation(current.mark))
      commit({
        ...document,
        annotations: [...document.annotations, current.mark],
      });
    else if (
      current.crop &&
      current.crop.width > 2 &&
      current.crop.height > 2
    ) {
      commit({ ...document, crop: current.crop });
      setTool("select");
    }
    gesture.current = null;
    setDraft(null);
    setCropDraft(null);
  };
  const save = (copy: boolean) => {
    if (
      !content.current ||
      saving ||
      text ||
      gesture.current ||
      (copy && !ready)
    )
      return;
    setSaving(true);
    setMessage("");
    try {
      const png = exportAnnotation(content.current, document.crop);
      // Submit the clipboard write synchronously in this user gesture.
      const operation = copy ? copyAnnotation(png) : downloadAnnotation(png);
      void operation
        .then(() => {
          if (copy) onCopied();
          else setMessage("PNG downloaded.");
        })
        .catch(() => {
          setMessage(
            copy
              ? "Could not copy. Allow clipboard access and retry, or download the PNG. Your edits are preserved."
              : "Could not export the PNG. Your edits are preserved; please retry.",
          );
        })
        .finally(() => setSaving(false));
    } catch {
      setMessage(
        "Could not export the PNG. Your edits are preserved; please retry.",
      );
      setSaving(false);
    }
  };
  const updateAppearance = (values: {
    color?: string;
    strokeWidth?: number;
    fontSize?: number;
  }) => {
    if (values.color) setColor(values.color);
    if (values.strokeWidth) setStrokeWidth(values.strokeWidth);
    if (values.fontSize) setFontSize(values.fontSize);
    if (selectedMark) change({ ...selectedMark, ...values });
  };
  return (
    <Dialog
      open
      onOpenChange={(open, details) => {
        if (open) return;
        details.cancel();
        if (saving) return;
        if (gesture.current) {
          cancelGesture();
          popup.current?.focus();
        } else requestClose();
      }}
    >
      <DialogPopup
        ref={popup}
        showCloseButton={false}
        className={styles.editor}
        finalFocus={false}
        initialFocus={() =>
          copyButton.current?.disabled ? popup.current : copyButton.current
        }
        onKeyDown={(event) => {
          event.stopPropagation();
          if (event.nativeEvent.isComposing) return;
          if (
            event.target instanceof HTMLInputElement ||
            event.target instanceof HTMLTextAreaElement ||
            event.target instanceof HTMLSelectElement
          )
            return;
          if (saving || text || confirmClose) return;
          const key = event.key.toLowerCase(),
            modifier = event.ctrlKey || event.metaKey;
          if (modifier && key === "z") {
            event.preventDefault();
            dispatch({ type: event.shiftKey ? "redo" : "undo" });
          } else if (modifier && key === "y") {
            event.preventDefault();
            dispatch({ type: "redo" });
          } else if (modifier && event.key === "Enter") {
            event.preventDefault();
            save(true);
          } else if (key === "delete" || key === "backspace") {
            event.preventDefault();
            remove();
          } else if (selectedMark && event.key.startsWith("Arrow")) {
            event.preventDefault();
            const step = event.shiftKey ? 10 : 1;
            change({
              ...selectedMark,
              x:
                selectedMark.x +
                (key === "arrowright" ? step : key === "arrowleft" ? -step : 0),
              y:
                selectedMark.y +
                (key === "arrowdown" ? step : key === "arrowup" ? -step : 0),
            });
          } else if (!modifier && !event.altKey) {
            const next = tools.find((item) => item.key === key);
            if (next) {
              event.preventDefault();
              selectTool(next.tool);
            }
          }
        }}
      >
        <header className={styles.header}>
          <div>
            <DialogTitle>Annotate screenshot</DialogTitle>
            <DialogDescription className={styles.description}>
              Copy, then paste into chat or another app.
            </DialogDescription>
          </div>
          <div className={styles.actions}>
            <Button
              size="icon"
              variant="ghost"
              aria-label="Download PNG"
              disabled={saving || !!text || !!draft || !!cropDraft}
              onClick={() => save(false)}
            >
              <DownloadSimple />
            </Button>
            <Button
              size="icon"
              ref={copyButton}
              aria-label="Copy PNG (Ctrl/⌘ Enter)"
              disabled={!ready || saving || !!text || !!draft || !!cropDraft}
              onClick={() => save(true)}
            >
              <Clipboard />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              aria-label="Close annotation"
              disabled={saving}
              onClick={requestClose}
            >
              <X />
            </Button>
          </div>
        </header>
        <div className={styles.body}>
          <div
            className={styles.toolbar}
            role="group"
            aria-label="Annotation tools"
          >
            <div className={styles.tools}>
              {tools.map(({ tool: value, label, key, icon: Icon }) => (
                <Button
                  key={value}
                  size="icon"
                  variant={tool === value ? "secondary" : "ghost"}
                  aria-label={`${label} (${key.toUpperCase()})`}
                  aria-pressed={tool === value}
                  disabled={saving || !!text}
                  onClick={() => selectTool(value)}
                >
                  <Icon />
                </Button>
              ))}
            </div>
            <div className={styles.actions}>
              <Button
                size="icon"
                variant="ghost"
                aria-label="Undo (Ctrl/⌘ Z)"
                disabled={!history.past.length || saving || !!text}
                onClick={() => dispatch({ type: "undo" })}
              >
                <ArrowBendUpLeft />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                aria-label="Redo (Ctrl/⌘ Shift Z)"
                disabled={!history.future.length || saving || !!text}
                onClick={() => dispatch({ type: "redo" })}
              >
                <ArrowBendUpRight />
              </Button>
            </div>
          </div>
          <div className={styles.options}>
            {tool !== "crop" && tool !== "mosaic" && tool !== "redact" && (
              <>
                <div
                  className={styles.colors}
                  role="group"
                  aria-label="Annotation color"
                >
                  {colors.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      aria-label={item.label}
                      title={item.label}
                      aria-pressed={
                        (selectedMark?.color ?? color) === item.value
                      }
                      className={styles.swatch}
                      style={{ background: item.value }}
                      disabled={saving || !!text}
                      onClick={() => updateAppearance({ color: item.value })}
                    />
                  ))}
                </div>
                <select
                  aria-label={
                    tool === "text" || selectedMark?.kind === "text"
                      ? "Font size"
                      : "Stroke width"
                  }
                  value={
                    tool === "text" || selectedMark?.kind === "text"
                      ? selectedMark?.kind === "text"
                        ? selectedMark.fontSize
                        : fontSize
                      : (selectedMark?.strokeWidth ?? strokeWidth)
                  }
                  disabled={saving || !!text}
                  onChange={(event) =>
                    updateAppearance(
                      tool === "text" || selectedMark?.kind === "text"
                        ? { fontSize: Number(event.target.value) }
                        : { strokeWidth: Number(event.target.value) },
                    )
                  }
                >
                  {(tool === "text" || selectedMark?.kind === "text"
                    ? [18, 24, 28, 36, 48, 64]
                    : [2, 4, 8, 12]
                  ).map((value) => (
                    <option key={value} value={value}>
                      {value} px
                    </option>
                  ))}
                </select>
              </>
            )}
            {tool === "crop" && (
              <Button
                size="icon"
                variant="ghost"
                aria-label="Restore full image"
                disabled={saving || !!text}
                onClick={() => commit({ ...document, crop: bounds })}
              >
                <SelectionAll />
              </Button>
            )}
            {tool === "redact" && <span>Drag to cover private details.</span>}
            {tool === "mosaic" && (
              <span>For sensitive text, use opaque redaction.</span>
            )}
            {selectedMark && (
              <>
                {selectedMark.kind === "text" && (
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label="Edit text"
                    disabled={saving || !!text}
                    onClick={() => setText(selectedMark)}
                  >
                    <TextT />
                  </Button>
                )}
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label="Delete annotation"
                  disabled={saving || !!text}
                  onClick={remove}
                >
                  <Trash />
                </Button>
              </>
            )}
            <div className={styles.spacer} />
            <select
              aria-label="Preview zoom"
              value={zoom}
              onChange={(event) => setZoom(event.target.value)}
            >
              <option value="fit">Fit</option>
              <option value="0.5">50%</option>
              <option value="1">100%</option>
              <option value="2">200%</option>
            </select>
          </div>
          <div ref={viewport} className={styles.viewport}>
            <div
              className={styles.paper}
              style={{
                width: document.crop.width * scale,
                height: document.crop.height * scale,
                cursor: tool === "select" ? "default" : "crosshair",
              }}
            >
              <AnnotationCanvas
                source={source}
                document={document}
                draft={draft}
                cropDraft={cropDraft}
                selected={selected}
                tool={tool}
                disabled={saving || !!text}
                editingText={text?.id ?? null}
                scale={scale}
                contentRef={content}
                onSelect={setSelected}
                onChange={change}
                onText={(mark) => {
                  if (mark.kind === "text") setText(mark);
                }}
                onStart={start}
                onMove={move}
                onEnd={end}
                onCancel={cancelGesture}
              />
              {text && (
                <AnnotationText
                  key={text.id}
                  mark={text}
                  crop={document.crop}
                  scale={scale}
                  onApply={finishText}
                  onCancel={() => {
                    setText(null);
                    popup.current?.focus();
                  }}
                />
              )}
            </div>
          </div>
        </div>
        <footer className={styles.footer}>
          <span role="status">
            {saving
              ? "Exporting PNG…"
              : text
                ? "Editing text · Ctrl/⌘ Enter to apply · Esc to cancel"
                : message ||
                  `${document.crop.width} × ${document.crop.height} px`}
          </span>
          {!!document.annotations.length && (
            <select
              aria-label="Select annotation"
              disabled={saving || !!text}
              value={selected ?? ""}
              onChange={(event) => {
                setSelected(event.target.value || null);
                setTool("select");
              }}
            >
              <option value="">
                Annotations ({document.annotations.length})
              </option>
              {document.annotations.map((mark, index) => (
                <option key={mark.id} value={mark.id}>
                  {index + 1}.{" "}
                  {mark.kind === "text" ? mark.text.slice(0, 24) : mark.kind}
                </option>
              ))}
            </select>
          )}
        </footer>
        <ModalFrame
          open={confirmClose}
          onOpenChange={setConfirmClose}
          title="Discard annotations?"
          description="Closing removes your local edits and undo history. Copy or download the PNG first if you want to keep it."
          closeLabel="Close confirmation"
          footer={
            <>
              <Button variant="outline" onClick={() => setConfirmClose(false)}>
                Keep editing
              </Button>
              <Button variant="destructive" onClick={onClose}>
                Discard
              </Button>
            </>
          }
        />
      </DialogPopup>
    </Dialog>
  );
}
