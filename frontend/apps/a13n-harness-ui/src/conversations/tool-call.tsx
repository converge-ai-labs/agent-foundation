import { createContext, memo, useContext, useMemo, useState } from "react";
import { Button, DisclosureSection } from "a13n-ui";
import {
  ArrowSquareOut,
  Check,
  Copy,
  FileText,
  PencilSimple,
  TerminalWindow,
  MagnifyingGlass,
  Code,
  Wrench,
} from "@phosphor-icons/react";
import { structuredPatch } from "diff";
import {
  describeTool,
  hostLookupPath,
  parsed,
  record,
  sourceText,
  type AppliedEdit,
  type ToolView,
} from "./tool-presentation";
import styles from "./tool-call.module.css";

// Human lookup on the WebUI Host, not an Environment-to-Host path mapping.
export const OpenHostFile = createContext<((path: string) => void) | undefined>(
  undefined,
);

function CodeContent({ title, value }: { title: string; value: unknown }) {
  const content = sourceText(value);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);
  return (
    <section className={styles.content} aria-label={title}>
      <header>
        <span>{title}</span>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={`Copy ${title.toLowerCase()}`}
          onClick={() => {
            void navigator.clipboard.writeText(content).then(
              () => {
                setCopied(true);
                setError(false);
              },
              () => setError(true),
            );
          }}
        >
          {copied ? <Check /> : <Copy />}
        </Button>
      </header>
      {error && (
        <small role="status">Copy unavailable. Select the text to copy.</small>
      )}
      <pre>{content}</pre>
    </section>
  );
}

export function editPatch(edit: AppliedEdit) {
  if (
    [edit.before, edit.after].some(
      (value) => value.length > 512 * 1024 || value.split("\n").length > 10001,
    )
  )
    return;
  return structuredPatch(
    edit.file_path,
    edit.file_path,
    edit.before,
    edit.after,
    undefined,
    undefined,
    { context: 3, maxEditLength: 2000, timeout: 100 },
  );
}
const EditDiff = memo(function EditDiff({ edit }: { edit: AppliedEdit }) {
  const patch = useMemo(() => editPatch(edit), [edit]);
  const [full, setFull] = useState(false);
  const lines = patch?.hunks.flatMap((hunk) => [
    `@@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@`,
    ...hunk.lines,
  ]);
  const added = patch?.hunks.reduce(
    (n, hunk) => n + hunk.lines.filter((line) => line.startsWith("+")).length,
    0,
  );
  const removed = patch?.hunks.reduce(
    (n, hunk) => n + hunk.lines.filter((line) => line.startsWith("-")).length,
    0,
  );
  return (
    <section className={styles.diff} aria-label="Applied edit">
      <header>
        <span>Applied edit</span>
        {patch && (
          <span>
            <span className={styles.addCount}>+{added}</span>{" "}
            <span className={styles.removeCount}>−{removed}</span>
          </span>
        )}
      </header>
      <small>Observed before/after content, not the current file.</small>
      {!lines ? (
        <p>
          Diff preview unavailable for this large change. Inspect the recorded
          content below.
        </p>
      ) : !lines.length ? (
        <p>No content change.</p>
      ) : (
        <pre>
          {(full ? lines : lines.slice(0, 100)).map((line, i) => (
            <span
              key={i}
              className={
                line.startsWith("+")
                  ? styles.add
                  : line.startsWith("-")
                    ? styles.remove
                    : line.startsWith("@@")
                      ? styles.hunk
                      : styles.context
              }
            >
              {line || " "}
            </span>
          ))}
        </pre>
      )}
      {lines && lines.length > 100 && (
        <Button size="sm" variant="ghost" onClick={() => setFull(!full)}>
          {full ? "Show fewer lines" : `Show all ${lines.length} lines`}
        </Button>
      )}
      <DisclosureSection title="Recorded content">
        <CodeContent title="Before" value={edit.before} />
        <CodeContent title="After" value={edit.after} />
      </DisclosureSection>
    </section>
  );
});

function ToolDetails({ tool }: { tool: ToolView }) {
  const info = describeTool(tool);
  const [raw, setRaw] = useState(false);
  const replacements =
    tool.name === "multi_edit" && Array.isArray(info.args.edits)
      ? info.args.edits.filter(record)
      : [info.args];
  return (
    <div className={styles.details}>
      {info.errorText && (
        <p role="status" className={styles.error}>
          {info.errorText}
        </p>
      )}
      {tool.edit && <EditDiff edit={tool.edit} />}
      {!tool.edit &&
        info.kind === "edit" &&
        replacements.some(
          (edit) =>
            typeof edit.old_string === "string" &&
            typeof edit.new_string === "string",
        ) && (
          <section>
            <h4>Requested replacement</h4>
            <small>
              Tool input only; an applied diff was not recorded in this view.
            </small>
            {replacements.map((edit, i) => (
              <div key={i}>
                {typeof edit.old_string === "string" && (
                  <CodeContent title="Find" value={edit.old_string} />
                )}
                {typeof edit.new_string === "string" && (
                  <CodeContent title="Replace with" value={edit.new_string} />
                )}
              </div>
            ))}
          </section>
        )}
      {info.kind === "shell" && (
        <>
          {typeof info.args.command === "string" && (
            <CodeContent title="Command" value={info.args.command} />
          )}
          {record(info.result.status) && (
            <small>
              Process: {sourceText(info.result.status.phase)}
              {typeof info.result.status.exit_code === "number"
                ? ` · Exit ${info.result.status.exit_code}`
                : ""}
            </small>
          )}
          {["stdout", "stderr"].map((name) =>
            record(info.result[name]) &&
            typeof info.result[name].text === "string" &&
            info.result[name].text ? (
              <CodeContent
                key={name}
                title={name}
                value={info.result[name].text}
              />
            ) : null,
          )}
          {info.outputIncomplete && (
            <p role="status">
              Partial process output. Earlier or omitted bytes are not shown.
            </p>
          )}
        </>
      )}
      {tool.name === "view" && typeof info.result.content === "string" && (
        <CodeContent title="Read content" value={info.result.content} />
      )}
      {tool.name === "write" && typeof info.args.content === "string" && (
        <CodeContent
          title={
            info.args.mode === "a" ? "Requested append" : "Requested content"
          }
          value={info.args.content}
        />
      )}
      {tool.name === "run_code" && typeof info.args.code === "string" && (
        <CodeContent title="Code" value={info.args.code} />
      )}
      <DisclosureSection title="Arguments & result">
        <div className={styles.format}>
          <Button
            size="sm"
            variant={raw ? "ghost" : "secondary"}
            aria-pressed={!raw}
            onClick={() => setRaw(false)}
          >
            Formatted
          </Button>
          <Button
            size="sm"
            variant={raw ? "secondary" : "ghost"}
            aria-pressed={raw}
            onClick={() => setRaw(true)}
          >
            Raw
          </Button>
        </div>
        {tool.inputOmitted ? (
          <p>Arguments omitted by the server.</p>
        ) : (
          tool.input !== undefined && (
            <CodeContent
              title="Arguments"
              value={raw ? tool.input : parsed(tool.input)}
            />
          )
        )}
        {tool.resultOmitted ? (
          <p>Result omitted by the server.</p>
        ) : (
          tool.result !== undefined && (
            <CodeContent
              title="Result"
              value={raw ? tool.result : parsed(tool.result)}
            />
          )
        )}
        {tool.result === undefined && !tool.resultOmitted && (
          <small>
            {info.phase}. This does not establish whether side effects occurred.
          </small>
        )}
      </DisclosureSection>
    </div>
  );
}
export const ToolCall = memo(function ToolCall({ tool }: { tool: ToolView }) {
  const info = describeTool(tool);
  const openFile = useContext(OpenHostFile);
  const path = hostLookupPath(info.path);
  const [expanded, setExpanded] = useState(false);
  const Icon = {
    file: FileText,
    edit: PencilSimple,
    shell: TerminalWindow,
    search: MagnifyingGlass,
    code: Code,
    tool: Wrench,
  }[info.kind];
  return (
    <section className={styles.tool} data-tool-id={tool.id}>
      <DisclosureSection
        open={expanded}
        onOpenChange={setExpanded}
        title={
          <span className={styles.title}>
            <Icon aria-hidden="true" />
            <strong>{info.label}</strong>
            {info.summary && <span title={info.summary}>{info.summary}</span>}
          </span>
        }
        summary={
          <span className={info.failed ? styles.error : styles.phase}>
            {info.phase}
          </span>
        }
      >
        {expanded && <ToolDetails tool={tool} />}
      </DisclosureSection>
      {openFile && path && (
        <Button
          className={styles.fileLink}
          size="sm"
          variant="ghost"
          title="Look up this exact path on the WebUI server/container, not the Agent Environment. Opens current Host content or your existing private buffer."
          onClick={() => openFile(path)}
        >
          <ArrowSquareOut />
          Open on host
        </Button>
      )}
    </section>
  );
});
