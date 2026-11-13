import { SubagentGlyph } from "./background-card";
import { MovedToBackground } from "./background-links";
import conversation from "./conversation.module.css";
import { createContext, memo, useContext, useMemo, useState } from "react";
import { AguiContent, Button, ScrollArea, DisclosureSection } from "a13n-ui";
import { Link } from "react-router";
import { useTranslation } from "../i18n";
import { useGuestAccess } from "../transport/context";
import { ArrowSquareOut, Check, Copy } from "@phosphor-icons/react";
import { structuredPatch } from "diff";
import {
  describeTool,
  toolLabel,
  toolSummary,
  questionReceipt,
  activityKind,
  activitySummary,
  hostLookupPath,
  parsed,
  record,
  sourceText,
  type AppliedEdit,
  type ToolView,
} from "./tool-presentation";
import styles from "./tool-call.module.css";
import { SyntaxCode, codeLanguage } from "./syntax-code";
import { AttachmentThumbnail } from "./attachment-thumbnail";
import { RetainedImagePreview } from "./image-preview";
import { copyText } from "../platform";
import { DebugData, DebugId } from "../shell/debug";
import { ToolWorkingState } from "./tool-working-state";

export function toolPhase(
  phase: string,
  t: (key: string, options?: Record<string, unknown>) => string,
) {
  const listed = /^(\d+) executions? listed$/.exec(phase);
  if (listed)
    return t(
      Number(listed[1]) === 1
        ? "{{count}} execution listed"
        : "{{count}} executions listed",
      { count: Number(listed[1]) },
    );
  return t(phase);
}

// Activity summaries contain only UI-generated text and numeric counts, not tool payloads.
function activityText(
  text: string,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  if (
    text === "Message accepted - new turn" ||
    text === "Message accepted - instruction"
  )
    return t(text);
  const segments = text.split(" - ");
  return segments
    .map((segment) => {
      const counted =
        /^(\d+) (operation|file|command|process operation|action|lookup|cancellation request)s?$/.exec(
          segment,
        );
      if (counted) {
        const count = Number(counted[1]);
        const noun = counted[2];
        return t(count === 1 ? `{{count}} ${noun}` : `{{count}} ${noun}s`, {
          count,
        });
      }
      const attention = /^(\d+) need attention$/.exec(segment);
      if (attention)
        return t("{{count}} need attention", { count: Number(attention[1]) });
      const phrase =
        /^(Running|Ran|Exploring|Explored) (\d+) (command|process operation|file|lookup)s?$/.exec(
          segment,
        );
      if (phrase) {
        const keys: Record<string, string> = {
          "Running command": "Running {{count, number}} commands",
          "Ran command": "Ran {{count, number}} commands",
          "Running process operation":
            "Running {{count, number}} process operations",
          "Ran process operation": "Ran {{count, number}} process operations",
          "Exploring file": "Exploring {{count, number}} files",
          "Explored file": "Explored {{count, number}} files",
          "Exploring lookup": "Exploring {{count, number}} lookups",
          "Explored lookup": "Explored {{count, number}} lookups",
        };
        const key = keys[`${phrase[1]} ${phrase[3]}`];
        if (key) return t(key, { count: Number(phrase[2]) });
      }
      return toolPhase(segment, t);
    })
    .join(" - ");
}

// Human lookup on the WebUI Host, not an Environment-to-Host path mapping.
export const OpenHostFile = createContext<((path: string) => void) | undefined>(
  undefined,
);

function CodeContent({
  title,
  value,
  language = "",
}: {
  title: string;
  value: unknown;
  language?: string;
}) {
  const { t } = useTranslation();
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
          aria-label={t("Copy {{title}}", { title: title.toLowerCase() })}
          onClick={() => {
            void copyText(content).then(
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
        <small role="status">
          {t("Copy unavailable. Select the text to copy.")}
        </small>
      )}
      <ScrollArea
        autoHeight
        scrollbarGutter
        clampContentMinWidth={false}
        className={`${styles.sourceScroll} ${conversation.innerReader}`}
        data-tool-source
      >
        <pre>
          <SyntaxCode
            source={content}
            language={language || (typeof value === "object" ? "json" : "")}
          />
        </pre>
      </ScrollArea>
    </section>
  );
}

export function editPatch(edit: AppliedEdit) {
  const patch = structuredPatch(
    edit.file_path,
    edit.file_path,
    edit.before,
    edit.after,
    undefined,
    undefined,
    { context: 3, maxEditLength: 2000, timeout: 100 },
  );
  if (patch) return patch;
  // Keep the computation bounded without omitting evidence: when minimal diff
  // calculation exceeds its budget, show a complete replacement in linear time.
  const replacement = (text: string, prefix: string) => {
    if (!text) return { count: 0, lines: [] };
    const lines = text.split("\n");
    if (text.endsWith("\n")) lines.pop();
    return {
      count: lines.length,
      lines: [
        ...lines.map((line) => `${prefix}${line}`),
        ...(text.endsWith("\n") ? [] : ["\\ No newline at end of file"]),
      ],
    };
  };
  const before = replacement(edit.before, "-");
  const after = replacement(edit.after, "+");
  return {
    hunks: [
      {
        oldStart: before.count ? 1 : 0,
        oldLines: before.count,
        newStart: after.count ? 1 : 0,
        newLines: after.count,
        lines: [...before.lines, ...after.lines],
      },
    ],
  };
}
const EditDiff = memo(function EditDiff({
  edit,
  applied = true,
}: {
  edit: AppliedEdit;
  applied?: boolean;
}) {
  const { t } = useTranslation();
  const title = applied ? t("Applied edit") : t("Requested replacement");
  const patch = useMemo(() => editPatch(edit), [edit]);
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
    <section className={styles.diff} aria-label={title}>
      <header>
        <span>{title}</span>
        {patch && (
          <span>
            <span className={styles.addCount}>+{added}</span>{" "}
            <span className={styles.removeCount}>−{removed}</span>
          </span>
        )}
      </header>
      <small>
        {applied
          ? t("Observed before/after content, not the current file.")
          : t("Requested fragment diff only; not proof of an applied change.")}
      </small>
      {!lines ? (
        <p>
          {t("Too large for a diff preview. Review the saved content below.")}
        </p>
      ) : !lines.length ? (
        <p>{t("No content change.")}</p>
      ) : (
        <pre>
          {lines.map((line, i) => (
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
      <DisclosureSection title={t("Recorded content")}>
        <CodeContent
          title={t("Before")}
          value={edit.before}
          language={codeLanguage(edit.file_path)}
        />
        <CodeContent
          title={t("After")}
          value={edit.after}
          language={codeLanguage(edit.file_path)}
        />
      </DisclosureSection>
    </section>
  );
});

function ToolImages({ tool }: { tool: ToolView }) {
  const { t } = useTranslation();
  const [preview, setPreview] =
    useState<NonNullable<ToolView["images"]>[number]>();
  return (
    <>
      {tool.images?.map((image) => (
        <button
          key={image.attachment.attachment_id}
          type="button"
          className={styles.screenshot}
          onClick={() => setPreview(image)}
          aria-label={t("Open captured desktop image")}
        >
          <AttachmentThumbnail
            threadId={image.thread_id}
            attachment={image.attachment}
          />
          <span>{t("Open screenshot")}</span>
        </button>
      ))}
      {tool.imageUnavailable && (
        <p role="status">
          {t("The model received the screenshot, but no preview was saved.")}
        </p>
      )}
      {preview && (
        <RetainedImagePreview
          threadId={preview.thread_id}
          attachment={preview.attachment}
          close={() => setPreview(undefined)}
        />
      )}
    </>
  );
}

/** Recorded arguments and result, formatted or exactly as received. */
function ToolData({ tool }: { tool: ToolView }) {
  const { t } = useTranslation();
  const info = describeTool(tool);
  const [raw, setRaw] = useState(false);
  return (
    <>
      <div className={styles.format}>
        <Button
          size="sm"
          variant={raw ? "ghost" : "secondary"}
          aria-pressed={!raw}
          onClick={() => setRaw(false)}
        >
          {t("Formatted")}
        </Button>
        <Button
          size="sm"
          variant={raw ? "secondary" : "ghost"}
          aria-pressed={raw}
          onClick={() => setRaw(true)}
        >
          {t("Raw")}
        </Button>
      </div>
      {tool.inputOmitted ? (
        <p>{t("Input omitted by the server.")}</p>
      ) : (
        tool.input !== undefined && (
          <CodeContent
            title={t("Input")}
            value={raw ? tool.input : parsed(tool.input)}
          />
        )
      )}
      {tool.resultOmitted ? (
        <p>{t("Result omitted by the server.")}</p>
      ) : (
        tool.result !== undefined && (
          <CodeContent
            title={t("Result")}
            value={raw ? tool.result : parsed(tool.result)}
          />
        )
      )}
      {tool.result === undefined &&
        !tool.resultParts &&
        !tool.resultOmitted && (
          <small>
            {t(
              "{{phase}}. This does not establish whether side effects occurred.",
              { phase: toolPhase(info.phase, t) },
            )}
          </small>
        )}
    </>
  );
}

export function ShellOutput({ tool }: { tool: ToolView }) {
  const { t } = useTranslation();
  const info = describeTool(tool);
  const textResult = parsed(tool.result);
  return (
    <section className={styles.shellResult} aria-label={t("Command result")}>
      {info.command && (
        <CodeContent
          title={t("Command")}
          value={info.command}
          language="bash"
        />
      )}
      {!tool.resultParts &&
        typeof textResult === "string" &&
        textResult &&
        !info.errorText && (
          <CodeContent title={t("Output")} value={textResult} />
        )}
      {["stdout", "stderr"].map((name) => {
        const stream = info.result[name];
        const value =
          typeof stream === "string"
            ? stream
            : record(stream)
              ? stream.text
              : undefined;
        return typeof value === "string" && value ? (
          <CodeContent key={name} title={name} value={value} />
        ) : null;
      })}
      {(record(info.result.status) || info.exitCode !== undefined) && (
        <small className={styles.processStatus}>
          {[
            record(info.result.status) &&
            typeof info.result.status.phase === "string"
              ? ((
                  {
                    running: t("Running"),
                    exited: t("Exited"),
                    signaled: t("Signaled"),
                    timed_out: t("Timed out"),
                    failed: t("Failed"),
                    cancelled: t("Cancelled"),
                  } as Record<string, string>
                )[info.result.status.phase] ?? info.result.status.phase)
              : "",
            info.exitCode !== undefined
              ? t("Exit {{code}}", { code: info.exitCode })
              : "",
          ]
            .filter(Boolean)
            .join(" - ")}
        </small>
      )}
      {info.outputIncomplete && (
        <p role="status">
          {t("Partial process output. Earlier or omitted bytes are not shown.")}
        </p>
      )}
    </section>
  );
}

function ToolDetails({ tool }: { tool: ToolView }) {
  const { t } = useTranslation();
  const info = describeTool(tool);
  const textResult = parsed(tool.result);
  const replacements =
    tool.name === "multi_edit" && Array.isArray(info.args.edits)
      ? info.args.edits.filter(record)
      : [info.args];
  return (
    <div className={styles.details} data-tool-details>
      <DebugId label={t("Tool call")} value={tool.toolCallId ?? tool.id} />
      <DebugData
        label={t("Tool call data")}
        value={{
          id: tool.id,
          tool_call_id: tool.toolCallId,
          subagent_run_id: tool.subagentRunId,
          name: tool.name,
          input: tool.input,
          result: tool.result,
          outcome: tool.outcome,
          failure: tool.failure,
        }}
      />
      {(info.errorText || info.failed) && (
        <p role="status" className={styles.error}>
          {tool.generatedFailure
            ? t("Tool input validation failed.")
            : info.errorText || toolPhase(info.phase, t)}
        </p>
      )}
      {info.executionReceipt && (
        <p role="status">
          {t(
            "Handed off with status {{status}}. Check activity for the result.",
            { status: info.executionReceipt.status },
          )}
        </p>
      )}
      {tool.provider && (
        <small>
          {t("Provider-run tool - {{provider}}", { provider: tool.provider })}
        </small>
      )}
      {tool.resultParts && <AguiContent parts={tool.resultParts} />}
      {!tool.resultParts &&
        !info.shell &&
        typeof textResult === "string" &&
        textResult &&
        !info.errorText && (
          <CodeContent title={t("Output")} value={textResult} />
        )}
      <CollaborationDetails tool={tool} />
      <ToolWorkingState tool={tool} />
      {((tool.images?.length ?? 0) > 0 || tool.imageUnavailable) && (
        <ToolImages tool={tool} />
      )}
      {tool.editOmitted && (
        <p role="status">
          {t(
            "This older edit has no saved diff. The current file may have changed since then.",
          )}
        </p>
      )}
      {tool.edit && tool.name !== "write" && <EditDiff edit={tool.edit} />}
      {tool.edit && tool.name === "write" && (
        <CodeContent
          title={t("Written content")}
          value={tool.edit.after}
          language={codeLanguage(tool.edit.file_path)}
        />
      )}
      {!tool.edit &&
        info.kind === "edit" &&
        !tool.editOmitted &&
        replacements.some(
          (edit) =>
            typeof edit.old_string === "string" &&
            typeof edit.new_string === "string",
        ) && (
          <section>
            {replacements.map((edit, i) =>
              typeof edit.old_string === "string" &&
              typeof edit.new_string === "string" ? (
                <EditDiff
                  key={i}
                  applied={false}
                  edit={{
                    file_path: String(info.args.file_path ?? ""),
                    before: edit.old_string,
                    after: edit.new_string,
                  }}
                />
              ) : null,
            )}
          </section>
        )}
      {info.shell && <ShellOutput tool={tool} />}
      {tool.name === "view" && typeof info.result.content === "string" && (
        <CodeContent
          title={t("Read content")}
          value={info.result.content}
          language={codeLanguage(String(info.args.file_path ?? ""))}
        />
      )}
      {!tool.edit &&
        tool.name === "write" &&
        typeof info.args.content === "string" && (
          <CodeContent
            title={
              info.args.mode === "a"
                ? t("Requested append")
                : t("Requested content")
            }
            value={info.args.content}
            language={codeLanguage(String(info.args.file_path ?? ""))}
          />
        )}
      {tool.name === "run_code" && typeof info.args.code === "string" && (
        <CodeContent
          title={t("Code")}
          value={info.args.code}
          language="python"
        />
      )}
      <DisclosureSection
        className={styles.recordedDisclosure}
        title={t("Input & result")}
      >
        <ToolData tool={tool} />
      </DisclosureSection>
    </div>
  );
}

/** One grouped call: a single row whose semantic details follow directly. */
function ToolOperation({ tool }: { tool: ToolView }) {
  const { t } = useTranslation();
  const detail = describeTool(tool);
  return (
    <section
      className={styles.operation}
      data-operation-id={tool.id}
      data-state={toolState(detail.phase)}
    >
      <header>
        <span
          className={styles.operationName}
          data-running-label={detail.running || undefined}
        >
          {toolLabel(detail, t)}
        </span>
        <span className={styles.operationSummary} title={detail.summary}>
          {toolSummary(detail, t)}
        </span>
        {detail.showPhase && (
          <small className={styles.phase}>{toolPhase(detail.phase, t)}</small>
        )}
        <MovedToBackground tool={tool} />
        <ToolActions tool={tool} />
      </header>
      <ToolDetails tool={tool} />
    </section>
  );
}
function toolState(phase: string) {
  if (phase === "Failed") return "failed";
  if (phase === "Result received") return "done";
  return "running";
}

function ToolActions({ tool }: { tool: ToolView }) {
  const { t } = useTranslation();
  const guest = useGuestAccess();
  const info = describeTool(tool);
  const openFile = useContext(OpenHostFile);
  const path = hostLookupPath(info.path);
  if (guest || (!info.threadId && !(openFile && path))) return null;
  return (
    <div className={styles.actions}>
      {info.threadId && (
        <Link
          className={styles.threadLink}
          to={`/threads/${encodeURIComponent(info.threadId)}`}
          title={info.threadId}
        >
          <ArrowSquareOut aria-hidden="true" />
          {info.threadTitle || t("Open chat")}
        </Link>
      )}
      {openFile && path && (
        <Button
          className={styles.fileLink}
          size="sm"
          variant="ghost"
          title={t(
            "Opens the server's copy of this file, or your unsaved draft. This may differ from the agent's copy.",
          )}
          onClick={() => openFile(path)}
        >
          <ArrowSquareOut />
          {t("Open on host")}
        </Button>
      )}
    </div>
  );
}

function CollaborationDetails({ tool }: { tool: ToolView }) {
  const { t } = useTranslation();
  const guest = useGuestAccess();
  const info = describeTool(tool);
  const { args, result } = info;
  const resources = ["threads", "projects", "agents", "models"].find(
    (key) => tool.name === `list_${key}` && Array.isArray(result[key]),
  );
  const project =
    tool.name === "get_project" && record(result.project)
      ? result.project
      : undefined;
  const selection = [
    ["Project", args.project_id === null ? t("No project") : args.project_id],
    ["Agent", args.agent_id],
    ["Model override", args.model_id],
  ].filter(([, value]) => typeof value === "string");
  return (
    <>
      {info.kind === "thread" && (
        <>
          {selection.length > 0 && (
            <dl className={styles.selection}>
              {selection.map(([label, value]) => (
                <div key={String(label)}>
                  <dt>{t(String(label))}</dt>
                  <dd>{String(value)}</dd>
                </div>
              ))}
            </dl>
          )}
          {typeof args.prompt === "string" && (
            <CodeContent title={t("Task")} value={args.prompt} />
          )}
          {typeof args.message === "string" && (
            <CodeContent title={t("Message")} value={args.message} />
          )}
          {[
            "create_thread",
            "run_thread",
            "send_thread_message",
            "steer_thread",
          ].includes(tool.name) && (
            <small>
              {t(
                "Check progress in the chat. Accepted doesn't mean finished or saved. Check the outcome before retrying.",
              )}
            </small>
          )}
        </>
      )}
      {resources && (
        <section aria-label={t(`Discovered ${resources}`)}>
          <h4>
            {typeof result.total === "number"
              ? t(`{{count}} ${resources} found`, { count: result.total })
              : t(`Available ${resources}`)}
          </h4>
          <ScrollArea
            autoHeight
            scrollbarGutter
            className={`${styles.resourceScroll} ${conversation.innerReader}`}
            data-tool-resources
          >
            <ul className={styles.resources}>
              {(result[resources] as unknown[])
                .filter(record)
                .map((resource, index) => {
                  const id =
                    typeof resource.thread_id === "string"
                      ? resource.thread_id
                      : typeof resource.id === "string"
                        ? resource.id
                        : "";
                  const name =
                    typeof resource.title === "string"
                      ? resource.title
                      : typeof resource.name === "string"
                        ? resource.name
                        : id;
                  return (
                    <li key={id || index}>
                      {!guest && resources === "threads" && id ? (
                        <Link to={`/threads/${encodeURIComponent(id)}`}>
                          {name || id}
                          <ArrowSquareOut aria-hidden="true" />
                        </Link>
                      ) : !guest && resources === "projects" && id ? (
                        <Link to={`/projects/${encodeURIComponent(id)}`}>
                          {name || id}
                        </Link>
                      ) : (
                        <span>{name || id}</span>
                      )}
                      <small>
                        {[
                          id !== name ? id : "",
                          resource.model_id,
                          resource.route,
                        ]
                          .filter((value) => typeof value === "string" && value)
                          .join(" - ")}
                      </small>
                    </li>
                  );
                })}
            </ul>
          </ScrollArea>
          {typeof result.next_cursor === "string" && (
            <small>{t("More results available; this is a loaded page.")}</small>
          )}
        </section>
      )}
      {project && (
        <section>
          <h4>
            {typeof project.name === "string" ? project.name : t("Project")}
          </h4>
          {Array.isArray(project.roots) && (
            <CodeContent title={t("Project roots")} value={project.roots} />
          )}
        </section>
      )}
    </>
  );
}

export const ToolCall = memo(function ToolCall({ tool }: { tool: ToolView }) {
  const { t } = useTranslation();
  const info = describeTool(tool);
  if (tool.name === "ask_user_question")
    return <QuestionInteraction tool={tool} />;
  return (
    <section
      className={styles.tool}
      data-tool-id={tool.id}
      data-state={toolState(info.phase)}
    >
      <DisclosureSection
        title={
          <span className={styles.title}>
            <strong data-running-label={info.running || undefined}>
              {toolLabel(info, t)}
            </strong>{" "}
            {tool.name === "spawn_subagent" &&
              typeof info.result.thread_id === "string" && (
                <SubagentGlyph id={info.result.thread_id} />
              )}
            {info.summary && (
              <span title={info.summary}>{toolSummary(info, t)}</span>
            )}
          </span>
        }
        summary={
          info.showPhase ? (
            <span className={styles.phase}>{toolPhase(info.phase, t)}</span>
          ) : undefined
        }
      >
        <ToolDetails tool={tool} />
      </DisclosureSection>
      <MovedToBackground tool={tool} />
      <ToolActions tool={tool} />
    </section>
  );
});

/** The same question stays visible before and after its durable result. */
export function QuestionInteraction({ tool }: { tool: ToolView }) {
  const { t } = useTranslation();
  const info = describeTool(tool);
  const receipt = questionReceipt(tool);
  const questions =
    receipt?.questions ??
    (Array.isArray(info.args.questions)
      ? info.args.questions.filter(record)
      : []);
  return (
    <section
      className={`${styles.tool} ${styles.questionReceipt}`}
      data-tool-id={tool.id}
      aria-label={receipt ? t("Answers") : t("Question")}
    >
      <header className={styles.questionStatus}>
        {receipt
          ? t("Answered")
          : tool.result !== undefined || tool.outcome || tool.failure
            ? t("Not answered")
            : t("Awaiting response")}
      </header>
      {!receipt && (
        <div className={styles.questionContext}>
          {questions.map((question, index) => (
            <div key={index}>
              {typeof question.header === "string" && (
                <strong className={styles.questionHeader}>
                  {question.header}
                </strong>
              )}
              {typeof question.question === "string" && (
                <p className={styles.questionText}>{question.question}</p>
              )}
            </div>
          ))}
          {(tool.failure || typeof tool.result === "string") && (
            <p className={styles.answerDescription}>
              {t("No answer was submitted.")}
            </p>
          )}
        </div>
      )}
      <dl className={styles.answers}>
        {receipt?.items.map((item, index) => (
          <div key={index}>
            <dt>
              <span className={styles.questionHeader}>
                {item.generatedTitle ? t("Response") : item.title}
              </span>
              {item.question && item.question !== item.title && (
                <p className={styles.questionText}>{item.question}</p>
              )}
            </dt>
            <dd>
              {item.values.map((value, i) => (
                <div key={i}>
                  <p className={styles.answerLabel}>{value.label}</p>
                  {value.description && (
                    <p className={styles.answerDescription}>
                      {value.description}
                    </p>
                  )}
                </div>
              ))}
            </dd>
          </div>
        ))}
      </dl>
      <DisclosureSection title={t("Questions & details")}>
        <div className={styles.details} data-tool-details>
          {questions.map((question, index) => (
            <section key={index} className={styles.originalQuestion}>
              {typeof question.header === "string" && (
                <h4>{question.header}</h4>
              )}
              {typeof question.question === "string" && (
                <p>{question.question}</p>
              )}
              {Array.isArray(question.options) && (
                <ul>
                  {question.options.filter(record).map((option, i) => (
                    <li key={i}>
                      {typeof option.label === "string" && (
                        <strong>{option.label}</strong>
                      )}
                      {typeof option.description === "string" && (
                        <span>{option.description}</span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ))}
          <ToolDetails tool={tool} />
        </div>
      </DisclosureSection>
    </section>
  );
}

/** One quiet disclosure for adjacent activity; expanding shows details without a second per-call accordion. */
export const ToolActivity = memo(function ToolActivity({
  tools,
}: {
  tools: ToolView[];
}) {
  const { t } = useTranslation();
  const kind = activityKind(tools[0]);
  if (!kind)
    return (
      <>
        {tools.map((tool) => (
          <ToolCall key={tool.id} tool={tool} />
        ))}
      </>
    );
  const info = activitySummary(tools);
  const single = tools.length === 1 ? describeTool(tools[0]) : undefined;
  const target = single && toolSummary(single, t);
  return (
    <section
      className={`${styles.tool} ${styles.group}`}
      data-activity={kind}
      data-tool-id={tools[0].id}
      data-state={
        tools.some((tool) => toolState(describeTool(tool).phase) === "running")
          ? "running"
          : undefined
      }
    >
      <DisclosureSection
        title={
          <span className={styles.title}>
            <strong data-running-label={info.running || undefined}>
              {kind === "work" && single
                ? toolLabel(single, t)
                : activityText(info.title, t)}
            </strong>{" "}
            {target && <span title={target}>{target}</span>}
          </span>
        }
        summary={
          info.status ? (
            <span className={styles.phase}>{activityText(info.status, t)}</span>
          ) : undefined
        }
      >
        <div className={styles.operations}>
          {tools.length === 1 ? (
            <>
              <ToolDetails tool={tools[0]} />
              <ToolActions tool={tools[0]} />
            </>
          ) : (
            tools.map((tool) => <ToolOperation key={tool.id} tool={tool} />)
          )}
        </div>
      </DisclosureSection>
      {tools.length === 1 && <MovedToBackground tool={tools[0]} />}
    </section>
  );
});
