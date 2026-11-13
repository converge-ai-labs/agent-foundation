import { BackgroundStateIcon } from "./background-state-icon";
import { TerminalWindow, Wrench, CirclesThree } from "@phosphor-icons/react";
import { ChildInputLabels } from "./input-content";
import { useLayoutEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { useBackground } from "./background-context";
import {
  backgroundKey,
  backgroundItems,
  SECTION_LABELS,
  targetSection,
  type BackgroundTarget,
  type BackgroundItem,
} from "./background-work";
import {
  BackgroundList,
  ChildStop,
  SubagentGlyph,
  ToolCancel,
} from "./background-card";
import { ConversationTranscript } from "./transcript";
import { sourceText, parsed, describeTool } from "./tool-presentation";
import styles from "./background.module.css";
import { ModelIdentity } from "../shell/model-identity";
import { modelControlMessage } from "./model-control-messages";
import { ViewLink } from "../native/view-link";
import { useHistory, useThread, transcriptHistory } from "./queries";
import { useChildControlState } from "./child-controls";
import { ShellOutput } from "./tool-call";

export function BackgroundPanel({ target }: { target: BackgroundTarget }) {
  const { t } = useTranslation();
  const bg = useBackground();
  const listed = bg.items.find(
    (item) => backgroundKey(item.target) === backgroundKey(target),
  );
  const { client } = useTransport();
  const captured = useQuery({
    queryKey: ["thread", target.threadId, "child-review", target.executionId],
    enabled:
      !listed && !bg.loading && target.kind === "child" && !!target.executionId,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/children", {
          params: {
            path: { thread_id: target.threadId },
            query: { execution_id: target.executionId },
          },
          signal,
        }),
      ),
  });
  const item =
    listed ??
    backgroundItems(
      target.threadId,
      captured.data?.executions ?? [],
      [],
      [],
      t,
    ).find((item) => backgroundKey(item.target) === backgroundKey(target));
  const section = targetSection(target);
  return (
    <section className={styles.panel} aria-label={target.title}>
      <header className={styles.panelHeader}>
        {target.kind === "child" ? (
          <SubagentGlyph id={target.id} running={item?.running} />
        ) : target.kind === "subagents" ? (
          <CirclesThree size={18} />
        ) : target.icon === "terminal" || target.kind === "process" ? (
          <TerminalWindow size={18} />
        ) : (
          <Wrench size={18} />
        )}
        <div className={styles.panelIdentity}>
          <h2 title={target.title}>
            {section ? t(SECTION_LABELS[section]) : target.title}
          </h2>
          {item?.child?.model && (
            <small className={styles.childModel}>
              <ModelIdentity
                name={item.child.model.name}
                modelIdentity={item.child.model.identity}
              />
              {modelControlMessage(
                item.child.model.thinking === "Provider default"
                  ? "Default"
                  : item.child.model.thinking,
                t,
              )}
            </small>
          )}
        </div>
        {section ? (
          <ViewLink
            getLink={() =>
              `${window.location.origin}/threads/${encodeURIComponent(target.threadId)}?native=${section}`
            }
          />
        ) : (
          <BackgroundStateIcon state={item?.state ?? "unavailable"} focusable />
        )}
        {item?.child && <ChildStop item={item} />}
        {item?.tool && <ToolCancel item={item} />}
      </header>
      {!section && <ErrorNotice error={bg.error} retry={bg.refresh} />}
      {section ? (
        <BackgroundList section={section} />
      ) : target.kind === "tool" ? (
        <ToolRecord target={target} item={item} />
      ) : !item ? (
        <p className={styles.empty}>
          {bg.loading || captured.isFetching
            ? t("Loading background work…")
            : t("This background item is unavailable.")}
        </p>
      ) : item.child ? (
        <ChildRecord item={item} />
      ) : (
        <div className={styles.reader}>
          <h3>{t("Command")}</h3>
          <pre>{item.process?.command || t("Command unavailable")}</pre>
          <p>
            {t("Last observed state")}:{" "}
            <BackgroundStateIcon state={item.state} focusable />
          </p>
          {item.process?.exitCode !== undefined && (
            <p>
              {t("Exit code")}: {item.process.exitCode}
            </p>
          )}
          <p>{t("Inspection does not control or stop processes.")}</p>
          <p>{t("Output is in the corresponding tool call.")}</p>
        </div>
      )}
    </section>
  );
}
function ChildRecord({ item }: { item: BackgroundItem }) {
  const { t } = useTranslation();
  const bg = useBackground();
  const child = item.child!;
  const live = bg.display?.childOutput(child);
  const checkpoint =
    item.running && live ? [...live.checkpoints.keys()].at(-1) : undefined;
  // Terminal publication changes the head without another request-boundary marker.
  const detail = useThread(child.child_thread_id, child.updated_at);
  const history = useHistory(
    child.child_thread_id,
    checkpoint ?? detail.data?.continuation_id,
    !!detail.data,
  );
  const source = history.data?.pages[0]?.continuation_id;
  const { entries, turns } = transcriptHistory(history.data?.pages);
  const { state: controlState, update: updateControl } = useChildControlState(
    child.parent_thread_id,
    child.execution_id,
  );
  const reader = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useLayoutEffect(() => {
    if (follow.current && reader.current)
      reader.current.scrollTop = reader.current.scrollHeight;
  });
  return (
    <>
      <div
        ref={reader}
        className={`${styles.reader} a13n-scrollbar`}
        onScroll={() => {
          const element = reader.current!;
          follow.current =
            element.scrollHeight - element.clientHeight - element.scrollTop <
            32;
        }}
      >
        <ErrorNotice
          error={history.error}
          retry={() => void history.refetch()}
        />
        {history.isPending &&
          !entries.length &&
          !live?.blocksAfter(source).length && (
            <p role="status">{t("Loading subagent conversation…")}</p>
          )}
        {history.hasNextPage && (
          <Button
            variant="ghost"
            size="sm"
            className={styles.loadEarlier}
            loading={history.isFetchingNextPage}
            onClick={() => {
              follow.current = false;
              void history.fetchNextPage();
            }}
          >
            {t("Load earlier messages")}
          </Button>
        )}
        <ChildInputLabels value={true}>
          <ConversationTranscript
            threadId={child.child_thread_id}
            entries={entries}
            turns={turns}
            blocks={item.running ? (live?.blocksAfter(source) ?? []) : []}
            localInputs={[]}
            continuation={source}
            loadDetails
            running={item.running}
            gap={item.running && live?.gap}
          />
        </ChildInputLabels>
        {child.failure && <p role="alert">{child.failure.message}</p>}
      </div>
      <ErrorNotice error={controlState.error} />
      {controlState.unknown && (
        <div className={styles.controlRecovery}>
          <p role="alert">
            {t("Couldn’t confirm delivery. Nothing was resent.")}
          </p>
          <Button variant="ghost" size="sm" onClick={bg.refresh}>
            {t("Refresh status")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              updateControl({
                unknown: false,
                error: undefined,
                outcome: undefined,
              })
            }
          >
            {t("Allow new action")}
          </Button>
        </div>
      )}
    </>
  );
}
function ToolRecord({
  target,
  item,
}: {
  target: BackgroundTarget;
  item?: BackgroundItem;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const output = useQuery({
    queryKey: [
      "thread",
      target.threadId,
      "tool-executions",
      "output",
      target.id,
      item?.state,
    ],
    queryFn: ({ signal }) =>
      result(
        client.GET(
          "/api/threads/{thread_id}/tool-executions/{execution_id}/output",
          {
            params: {
              path: {
                thread_id: target.threadId,
                execution_id: target.id,
              },
            },
            signal,
          },
        ),
      ),
    refetchInterval: (query) =>
      (item?.running ?? query.state.data?.live) ? 2000 : false,
  });
  const input = item?.call?.input ?? parsed(output.data?.input);
  const tool = {
    id: target.id,
    name: item?.tool?.tool_name ?? output.data?.execution?.tool_name ?? "",
    input,
  };
  const info = describeTool(tool);
  return (
    <div className={`${styles.reader} a13n-scrollbar`}>
      <section>
        <h3>{t("Input")}</h3>
        <pre>
          {input != null
            ? info.shell
              ? info.command
              : sourceText(input)
            : t("Input unavailable")}
        </pre>
        {output.data?.input_truncated && <p>{t("Input truncated.")}</p>}
      </section>
      <section>
        <h3>{output.data?.live ? t("Live output") : t("Output")}</h3>
        <ErrorNotice error={output.error} retry={() => void output.refetch()} />
        {output.isPending && <p role="status">{t("Loading output…")}</p>}
        {output.data?.state === "pending" && (
          <p>
            {t(
              "Live output is unavailable for this tool. Its result will appear after completion.",
            )}
          </p>
        )}
        {output.data?.state === "expired" && <p>{t("Output expired.")}</p>}
        {output.data?.state === "unavailable" && (
          <p>{t("Output unavailable.")}</p>
        )}
        {output.data?.truncated && (
          <p role="status">
            {t("Output truncated. Showing the retained preview.")}
          </p>
        )}
        {output.data?.text != null &&
          (info.shell ? (
            <ShellOutput
              tool={{ ...tool, input: undefined, result: output.data.text }}
            />
          ) : (
            <pre>{sourceText(parsed(output.data.text))}</pre>
          ))}
      </section>
    </div>
  );
}
