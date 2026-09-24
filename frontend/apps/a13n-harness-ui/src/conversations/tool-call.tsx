import { createContext, memo, useContext, useMemo, useState } from "react";
import { Button, DisclosureSection } from "a13n-ui";
import { Link } from "react-router";
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
  Globe,
  Chats,
  ListChecks,
  UsersThree,
} from "@phosphor-icons/react";
import { structuredPatch } from "diff";
import {
  describeTool,
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
      <pre>
        <SyntaxCode
          source={content}
          language={language || (typeof value === "object" ? "json" : "")}
        />
      </pre>
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
  const title = applied ? "Applied edit" : "Requested replacement";
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
          ? "Observed before/after content, not the current file."
          : "Requested fragment diff only; not proof of an applied change."}
      </small>
      {!lines ? (
        <p>
          Diff preview unavailable for this large change. Inspect the recorded
          content below.
        </p>
      ) : !lines.length ? (
        <p>No content change.</p>
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
      <DisclosureSection title="Recorded content">
        <CodeContent
          title="Before"
          value={edit.before}
          language={codeLanguage(edit.file_path)}
        />
        <CodeContent
          title="After"
          value={edit.after}
          language={codeLanguage(edit.file_path)}
        />
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
    <div className={styles.details} data-tool-details>
      {info.errorText && (
        <p role="status" className={styles.error}>
          {info.errorText}
        </p>
      )}
      {tool.provider && <small>Provider-run tool · {tool.provider}</small>}
      <CollaborationDetails tool={tool} />
      {tool.editOmitted && (
        <p role="status">
          This older record did not retain the applied edit content. Current
          Host content is not a historical diff.
        </p>
      )}
      {tool.edit && tool.name !== "write" && <EditDiff edit={tool.edit} />}
      {tool.edit && tool.name === "write" && (
        <CodeContent
          title="Written content"
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
      {info.kind === "shell" && (
        <>
          {typeof info.args.command === "string" && (
            <CodeContent
              title="Command"
              value={info.args.command}
              language="bash"
            />
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
        <CodeContent
          title="Read content"
          value={info.result.content}
          language={codeLanguage(String(info.args.file_path ?? ""))}
        />
      )}
      {!tool.edit &&
        tool.name === "write" &&
        typeof info.args.content === "string" && (
          <CodeContent
            title={
              info.args.mode === "a" ? "Requested append" : "Requested content"
            }
            value={info.args.content}
            language={codeLanguage(String(info.args.file_path ?? ""))}
          />
        )}
      {tool.name === "run_code" && typeof info.args.code === "string" && (
        <CodeContent title="Code" value={info.args.code} language="python" />
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
const toolIcons = {
  file: FileText,
  edit: PencilSimple,
  shell: TerminalWindow,
  search: MagnifyingGlass,
  web: Globe,
  thread: Chats,
  code: Code,
  tool: Wrench,
};

function ToolActions({ tool }: { tool: ToolView }) {
  const info = describeTool(tool);
  const openFile = useContext(OpenHostFile);
  const path = hostLookupPath(info.path);
  if (!info.threadId && !(openFile && path)) return null;
  return (
    <div className={styles.actions}>
      {info.threadId && (
        <Link
          className={styles.threadLink}
          to={`/threads/${encodeURIComponent(info.threadId)}`}
          title={info.threadId}
        >
          <ArrowSquareOut aria-hidden="true" />
          {info.threadTitle || "Open conversation"}
        </Link>
      )}
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
    </div>
  );
}

function CollaborationDetails({ tool }: { tool: ToolView }) {
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
    ["Project", args.project_id === null ? "No project" : args.project_id],
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
                  <dt>{String(label)}</dt>
                  <dd>{String(value)}</dd>
                </div>
              ))}
            </dl>
          )}
          {typeof args.prompt === "string" && (
            <CodeContent title="Task" value={args.prompt} />
          )}
          {typeof args.message === "string" && (
            <CodeContent title="Message" value={args.message} />
          )}
          {[
            "create_thread",
            "run_thread",
            "send_thread_message",
            "steer_thread",
          ].includes(tool.name) && (
            <small>
              Acceptance is not completion or saved delivery. Open the
              conversation to inspect progress; do not blindly retry an
              uncertain operation.
            </small>
          )}
        </>
      )}
      {resources && (
        <section aria-label={`Discovered ${resources}`}>
          <h4>
            {typeof result.total === "number"
              ? `${result.total} ${resources} found`
              : `Available ${resources}`}
          </h4>
          <ul className={styles.resources} data-tool-resources>
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
                    {resources === "threads" && id ? (
                      <Link to={`/threads/${encodeURIComponent(id)}`}>
                        {name || id}
                        <ArrowSquareOut aria-hidden="true" />
                      </Link>
                    ) : resources === "projects" && id ? (
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
                        .join(" · ")}
                    </small>
                  </li>
                );
              })}
          </ul>
          {typeof result.next_cursor === "string" && (
            <small>More results available; this is a loaded page.</small>
          )}
        </section>
      )}
      {project && (
        <section>
          <h4>{typeof project.name === "string" ? project.name : "Project"}</h4>
          {Array.isArray(project.roots) && (
            <CodeContent title="Project roots" value={project.roots} />
          )}
        </section>
      )}
    </>
  );
}

export const ToolCall = memo(function ToolCall({ tool }: { tool: ToolView }) {
  const info = describeTool(tool);
  const [expanded, setExpanded] = useState(false);
  const Icon = toolIcons[info.kind];
  if (tool.name === "ask_user_question")
    return <QuestionInteraction tool={tool} />;
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
          <span className={styles.phase}>
            {info.phase === "Result received" || info.phase === "Failed"
              ? ""
              : info.phase}
          </span>
        }
      >
        {expanded && <ToolDetails tool={tool} />}
      </DisclosureSection>
      <ToolActions tool={tool} />
    </section>
  );
});

/** The same question stays visible before and after its durable result. */
export function QuestionInteraction({ tool }: { tool: ToolView }) {
  const info = describeTool(tool);
  const [expanded, setExpanded] = useState(false);
  const receipt = questionReceipt(tool);
  const questions =
    receipt?.questions ??
    (Array.isArray(info.args.questions)
      ? info.args.questions.filter(record)
      : []);
  return (
    <section
      className={styles.questionReceipt}
      data-tool-id={tool.id}
      aria-label={receipt ? "Answers" : "Question"}
    >
      <header className={styles.questionStatus}>
        {receipt
          ? "Answered"
          : tool.result !== undefined || tool.outcome || tool.failure
            ? "Not answered"
            : "Awaiting response"}
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
              {tool.failure || (tool.result as string)}
            </p>
          )}
        </div>
      )}
      <dl className={styles.answers}>
        {receipt?.items.map((item, index) => (
          <div key={index}>
            <dt>
              <span className={styles.questionHeader}>{item.title}</span>
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
      <DisclosureSection
        title="Questions & details"
        open={expanded}
        onOpenChange={setExpanded}
      >
        {expanded && (
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
        )}
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
  const [expanded, setExpanded] = useState(false);
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
  const Icon = {
    explore: MagnifyingGlass,
    shell: TerminalWindow,
    web: Globe,
    edit: PencilSimple,
    work: ListChecks,
    subagents: UsersThree,
  }[kind];
  return (
    <section
      className={`${styles.tool} ${styles.group}`}
      data-activity={kind}
      data-tool-id={tools[0].id}
    >
      <DisclosureSection
        open={expanded}
        onOpenChange={setExpanded}
        title={
          <span className={styles.title}>
            <Icon aria-hidden="true" />
            <strong>{info.title}</strong>
          </span>
        }
        summary={
          info.status ? (
            <span className={styles.phase}>{info.status}</span>
          ) : undefined
        }
      >
        {expanded && (
          <div className={styles.operations}>
            {tools.map((tool) => {
              const detail = describeTool(tool);
              return (
                <section
                  key={tool.id}
                  className={styles.operation}
                  data-operation-id={tool.id}
                >
                  <header>
                    <span className={styles.operationName}>{detail.label}</span>
                    {detail.summary && (
                      <span title={detail.summary}>{detail.summary}</span>
                    )}
                    {detail.phase !== "Result received" && (
                      <small className={styles.phase}>{detail.phase}</small>
                    )}
                  </header>
                  <ToolActions tool={tool} />
                  <ToolDetails tool={tool} />
                </section>
              );
            })}
          </div>
        )}
      </DisclosureSection>
    </section>
  );
});
