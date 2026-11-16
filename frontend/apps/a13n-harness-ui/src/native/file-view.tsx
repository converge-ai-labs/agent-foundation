import { useTranslation } from "../i18n";
import {
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { createPortal } from "react-dom";
import {
  Button,
  FileTypeIcon,
  Tooltip,
  TooltipTrigger,
  TooltipPopup,
} from "a13n-ui";
import {
  ArrowClockwise,
  DownloadSimple,
  MagnifyingGlass,
  ArrowBendDownLeft,
} from "@phosphor-icons/react";
import { result } from "../transport/client";
import { useTransport } from "../transport/context";
import { SourceEditor } from "../configuration/editor";
import type {
  EditorCursor,
  SourceEditorHandle,
} from "../configuration/editor-view";
import { ErrorNotice } from "../shell/ui";
import { DebugOnly } from "../shell/debug";
import { MessageText } from "../conversations/message-text";
import { basename, FileBuffer, FileBuffers, type LineRange } from "./buffer";
import { CaptureContext } from "./capture";
import { DocumentPreview } from "./document-preview";
import {
  documentKind,
  documentLimit,
  unavailablePreview,
} from "./document-kind";
import { FileMedia } from "./file-media";
import { downloadFile, fileTransfer } from "./file-transfer";
import { mediaKind, MAX_MEDIA_BYTES } from "./media-kind";
import { modifierLabel } from "../platform";
import { isFindShortcut } from "../platform/find-shortcut";
import styles from "./native.module.css";

type FileViewProps = {
  toolbarHost?: HTMLDivElement | null;
  path: string;
  threadId?: string;
  open: (path: string) => void;
  line?: number;
};
export function FileView(props: FileViewProps) {
  const { t } = useTranslation();
  return (
    <div className={styles.previewStage} tabIndex={-1}>
      <div
        className={styles.loadingPreview}
        role="status"
        aria-label={t("Reading file…")}
      >
        <div className={styles.loadingMark} aria-hidden="true">
          <FileTypeIcon name={props.path} size={24} />
        </div>
        <span>{basename(props.path)}</span>
        <div className={styles.loadingTrack} aria-hidden="true" />
      </div>
      <FileContent {...props} />
    </div>
  );
}
function FileContent({
  toolbarHost,
  path,
  threadId,
  open,
  line,
}: FileViewProps) {
  const { t } = useTranslation();
  const transport = useTransport();
  const { client } = transport;
  const buffers = useContext(FileBuffers);
  const [error, setError] = useState<unknown>(null);
  const [wrap, setWrap] = useState(() => /\.(?:md|markdown|txt)$/i.test(path));
  const editor = useRef<SourceEditorHandle>(null);
  const preview = useRef<HTMLElement>(null);
  const [cursor, setCursor] = useState<EditorCursor>();
  const [imageAttempt, setImageAttempt] = useState(0);
  const [chosenMode, setMode] = useState<"preview" | "text">();
  const [textOpened, setTextOpened] = useState(false);
  const [selection, setSelection] = useState<LineRange>();
  useEffect(() => {
    if (line !== undefined) {
      setMode("text");
      setTextOpened(true);
    }
  }, [line]);
  const read = useQuery({
    queryKey: ["native", "text", path],
    queryFn: async ({ signal }) => {
      const info = await result(
        client.GET("/api/host/files/info", {
          params: { query: { path } },
          signal,
        }),
      );
      // .ts is both TypeScript and an MPEG transport stream suffix. The text
      // endpoint checks the actual bytes; binary streams keep their media view.
      const next =
        unavailablePreview(info.resolved_path) ||
        documentKind(info.resolved_path) ||
        (mediaKind(info.media_type) && !/\.ts$/i.test(path))
          ? info
          : {
              ...(await result(
                client.GET("/api/host/files/text", {
                  params: {
                    query: { path, expected_revision: info.entry.revision },
                  },
                  signal,
                }),
              )),
              media_type: info.media_type,
            };
      const existing = buffers.get(path);
      if (existing) existing.observe(next);
      else buffers.set(path, new FileBuffer(next));
      return next;
    },
    refetchOnMount: "always",
  });
  const buffer = buffers.get(path);
  const hasContent = !!buffer;
  const editorReady = cursor !== undefined;
  useLayoutEffect(() => {
    const element = preview.current;
    const stage = element?.parentElement;
    if (!element || !stage) return;
    const active = element.ownerDocument.activeElement;
    // The editor loads lazily and hides the content until ready. Keep focus
    // on its stage meanwhile, without stealing a newer focus choice.
    if (active === element.ownerDocument.body || active === stage)
      (editorReady ? element : stage).focus({ preventScroll: true });
  }, [hasContent, editorReady]);
  if (!buffer)
    return (
      <>
        <ErrorNotice error={read.error} retry={() => void read.refetch()} />
        {read.isPending && <span hidden data-file-pending="" />}
      </>
    );
  const symlink = buffer.observed.resolved_path !== path;
  const download = async () => {
    setError(null);
    try {
      const access = await fileTransfer(
        transport,
        path,
        buffer.observed.entry.revision,
        "attachment",
      );
      downloadFile(access.url);
    } catch (failure) {
      setError(failure);
    }
  };
  const isText = buffer.observed.presentation === "text";
  const markdown = isText && /\.(?:md|markdown)$/i.test(path);
  const unsupported = unavailablePreview(buffer.observed.resolved_path);
  const svg = buffer.observed.media_type === "image/svg+xml";
  const mode =
    chosenMode ??
    ((markdown || svg) && line === undefined ? "preview" : "text");
  const document = documentKind(buffer.observed.resolved_path);
  const media = svg
    ? "image"
    : !isText
      ? mediaKind(buffer.observed.media_type)
      : null;
  const refreshContent = async () => {
    const revision = buffer.observed.entry.revision;
    const next = await read.refetch();
    // A changed revision already replaces the preview; an unchanged one must
    // still allow retrying a failed fetch or decode.
    if (next.isSuccess && next.data.entry.revision === revision)
      setImageAttempt((value) => value + 1);
  };
  const toolbar = (
    <div className={styles.fileToolbar}>
      <div className={styles.actions} aria-label={t("File actions")}>
        {isText && mode === "text" && (
          <>
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    size="icon-sm"
                    variant={wrap ? "secondary" : "ghost"}
                  />
                }
                aria-label={t("Wrap long lines")}
                aria-pressed={wrap}
                onClick={() => {
                  setWrap(!wrap);
                  editor.current?.focus();
                }}
              >
                <ArrowBendDownLeft />
              </TooltipTrigger>
              <TooltipPopup>{t("Wrap long lines")}</TooltipPopup>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger
                render={<Button size="icon-sm" variant="ghost" />}
                aria-label={t("Find")}
                onClick={() => editor.current?.find()}
              >
                <MagnifyingGlass />
              </TooltipTrigger>
              <TooltipPopup>
                {t("Find in file ({{shortcut}} F)", {
                  shortcut: modifierLabel(),
                })}
              </TooltipPopup>
            </Tooltip>
          </>
        )}
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("Refresh")}
          title={t("Refresh file")}
          onClick={() => void refreshContent()}
          disabled={read.isFetching}
        >
          <ArrowClockwise />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("Download")}
          title={t("Download file")}
          onClick={() => void download()}
          disabled={read.isFetching || !!read.error}
        >
          <DownloadSimple />
        </Button>
      </div>
      {isText && (markdown || svg) && (
        <div className={styles.viewControls} aria-label={t("File view")}>
          <Button
            variant={mode === "preview" ? "secondary" : "ghost"}
            size="sm"
            aria-pressed={mode === "preview"}
            onClick={() => setMode("preview")}
          >
            {t("Preview")}
          </Button>
          <Button
            variant={mode === "text" ? "secondary" : "ghost"}
            size="sm"
            aria-pressed={mode === "text"}
            onClick={() => {
              setMode("text");
              setTextOpened(true);
            }}
          >
            {t("Text")}
          </Button>
        </div>
      )}
    </div>
  );
  return (
    <section
      ref={preview}
      tabIndex={-1}
      className={styles.file}
      aria-label={t("File content")}
      onKeyDownCapture={(event) => {
        if (
          mode !== "text" ||
          !isText ||
          !editor.current ||
          !isFindShortcut(event.nativeEvent) ||
          (event.target instanceof Element &&
            event.target.closest(".cm-editor"))
        )
          return;
        event.preventDefault();
        event.stopPropagation();
        editor.current.find();
      }}
    >
      {symlink && (
        <div className={styles.notice}>
          {t("Resolved path: {{path}}", {
            path: buffer.observed.resolved_path,
          })}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => open(buffer.observed.resolved_path)}
          >
            {t("Open target")}
          </Button>
        </div>
      )}
      {toolbarHost ? createPortal(toolbar, toolbarHost) : toolbar}
      <ErrorNotice
        error={error || read.error}
        retry={() => void read.refetch()}
      />
      {unsupported ||
      (!media && !document && buffer.observed.presentation === "binary") ? (
        <div className={styles.unsupportedPreview} role="status">
          <span className={styles.previewFileMark}>
            <FileTypeIcon name={path} size={26} />
          </span>
          <strong>{t("Preview not supported")}</strong>
          <span>{t("Download the original to open it in another app.")}</span>
        </div>
      ) : isText ? (
        <>
          {mode === "preview" && markdown && (
            <article
              className={styles.markdownPreview}
              aria-label={t("Markdown preview")}
            >
              <MessageText text={buffer.observed.text ?? ""} />
            </article>
          )}
          {mode === "preview" && svg && (
            <FileMedia
              key={buffer.observed.entry.revision + imageAttempt}
              kind="image"
              svg={true}
              svgText={buffer.observed.text ?? ""}
              path={path}
              revision={buffer.observed.entry.revision}
              retry={() => void refreshContent()}
            />
          )}
          {(textOpened || mode === "text") && (
            <div className={styles.editor} hidden={mode !== "text"}>
              <SourceEditor
                ref={editor}
                onCursor={setCursor}
                value={buffer.observed.text ?? ""}
                language="plain"
                filename={path}
                line={line}
                wrap={wrap}
                position={buffer.position}
                onPosition={(position) => {
                  buffer.position = position;
                }}
                label={t("File text: {{path}}", { path })}
                fill
                readOnly
                onSelection={setSelection}
              />
            </div>
          )}
        </>
      ) : document ? (
        buffer.observed.entry.size <= documentLimit ? (
          <DocumentPreview
            key={path + buffer.observed.entry.revision + imageAttempt}
            path={path}
            revision={buffer.observed.entry.revision}
            kind={document}
          />
        ) : (
          <p>{t("Document exceeds the 16 MiB preview limit.")}</p>
        )
      ) : media ? (
        media !== "image" || buffer.observed.entry.size <= MAX_MEDIA_BYTES ? (
          <FileMedia
            key={`${path}:${buffer.observed.entry.revision}:${imageAttempt}`}
            path={path}
            revision={buffer.observed.entry.revision}
            retry={() => void refreshContent()}
            kind={media}
            svg={svg}
          />
        ) : (
          <div className={styles.empty}>
            <h3>{t("Image exceeds the preview limit")}</h3>
            <p>
              {t(
                "Image too large to preview. Download it to view. The preview limit is 10 MiB.",
              )}
            </p>
          </div>
        )
      ) : (
        <div className={styles.empty}>
          <h3>{t("File exceeds the text preview limit")}</h3>
          <p>
            {t(
              "Preview UTF-8 text files up to 512 KiB, without NUL characters. Upload or attach files up to 10 MiB. Download larger files to view them.",
            )}
          </p>
        </div>
      )}
      <footer className={styles.fileFooter}>
        <CaptureContext
          key={`${path}:${buffer.observed.entry.revision}`}
          source={{
            file: {
              ...buffer.observed,
              entry: { ...buffer.observed.entry, path },
            },
          }}
          threadId={threadId}
          selection={mode === "text" ? selection : undefined}
          compact
          disabled={
            read.isFetching ||
            !!read.error ||
            buffer.observed.entry.size > 10 * 1024 * 1024
          }
        />
        {isText && mode === "text" && (
          <div className={styles.editorStatus}>
            {cursor && (
              <Button
                variant="ghost"
                size="sm"
                aria-label={t(
                  "Go to line, current line {{line}}, column {{column}}",
                  { line: cursor.line, column: cursor.column },
                )}
                title={t("Go to line ({{shortcut}} G)", {
                  shortcut: modifierLabel(),
                })}
                onClick={() => editor.current?.goToLine()}
              >
                {t("Ln {{line}}, Col {{column}}", {
                  line: cursor.line,
                  column: cursor.column,
                })}
                {cursor.selected > 0 &&
                  t(" - {{selected}} selected", {
                    selected: cursor.selected.toLocaleString(),
                  })}
              </Button>
            )}
          </div>
        )}
      </footer>
      <details className={styles.metadata}>
        <summary>{t("File details")}</summary>
        <p>
          {t("{{size}} bytes - {{type}}", {
            size: buffer.observed.entry.size.toLocaleString(),
            type:
              media ??
              (buffer.observed.presentation ?? "binary").replace("_", " "),
          })}
        </p>
        <p>{path}</p>
        {buffer.observed.resolved_path !== path && (
          <p>
            {t("Resolved path: {{path}}", {
              path: buffer.observed.resolved_path,
            })}
          </p>
        )}
        <DebugOnly>
          <p>
            {t("Server file - version {{revision}}", {
              revision: buffer.observed.entry.revision,
            })}
          </p>
        </DebugOnly>
      </details>
    </section>
  );
}
