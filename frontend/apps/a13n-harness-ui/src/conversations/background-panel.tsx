import conversation from "./conversation.module.css";
import {
  ArrowUp,
  TerminalWindow,
  Wrench,
  Stop as StopIcon,
} from "@phosphor-icons/react";
import { ChildInputLabels } from "./input-content";
import { useLayoutEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Textarea } from "a13n-ui";
import { useTranslation } from "../i18n";
import { useTransport, useGuestAccess } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { useBackground } from "./background-context";
import {
  backgroundKey,
  type BackgroundTarget,
  type BackgroundItem,
} from "./background-work";
import {
  SubagentGlyph,
  ToolCancel,
  useBackgroundStateLabel,
} from "./background-card";
import { useChildControl } from "./child-controls";
import { ConversationTranscript } from "./transcript";
import { sourceText, parsed, describeTool } from "./tool-presentation";
import styles from "./background.module.css";
import { useHistory, useThread, transcriptHistory } from "./queries";
import { ShellOutput } from "./tool-call";

export function BackgroundPanel({ target }: { target: BackgroundTarget }) {
  const { t } = useTranslation();
  const bg = useBackground();
  const state = useBackgroundStateLabel();
  const item = bg.items.find(
    (item) => backgroundKey(item.target) === backgroundKey(target),
  );
  return (
    <section className={styles.panel} aria-label={target.title}>
      <header className={styles.panelHeader}>
        {target.kind === "child" && (
          <SubagentGlyph id={target.id} running={item?.running} />
        )}
        {target.kind !== "child" &&
          (target.icon === "terminal" || target.kind === "process" ? (
            <TerminalWindow size={18} />
          ) : (
            <Wrench size={18} />
          ))}
        <h2 title={target.title}>{target.title}</h2>
        <span>{item ? state(item.state) : t("Unavailable")}</span>
        {item?.tool && <ToolCancel item={item} />}
      </header>
      <ErrorNotice error={bg.error} retry={bg.refresh} />
      {!item ? (
        <p className={styles.empty}>
          {bg.loading
            ? t("Loading background work…")
            : t("This background item is unavailable.")}
        </p>
      ) : item.child ? (
        <ChildRecord item={item} />
      ) : item.tool ? (
        <ToolRecord item={item} />
      ) : (
        <div className={styles.reader}>
          <h3>{t("Command")}</h3>
          <pre>{item.process?.command || t("Command unavailable")}</pre>
          <p>
            {t("Last observed state")}: {state(item.state)}
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
  const guest = useGuestAccess();
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
  const { controlState, updateControl, control } = useChildControl(
    child,
    bg.refresh,
  );
  const hasMessage = !!controlState.instruction.trim();
  const stopAction = !hasMessage && child.available_actions?.includes("cancel");
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
        {history.isPending && (
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
            gap={live?.gap}
          />
        </ChildInputLabels>
        {child.failure && <p role="alert">{child.failure.message}</p>}
      </div>
      <ErrorNotice error={controlState.error} />
      {controlState.unknown && (
        <div>
          <p role="alert">
            {t("Couldn’t confirm delivery. Nothing was resent.")}
          </p>
          <Button variant="ghost" size="sm" onClick={bg.refresh}>
            {t("Refresh status")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              updateControl({
                unknown: false,
                error: undefined,
                outcome: undefined,
              });
              control.reset();
            }}
          >
            {t("Allow new action")}
          </Button>
        </div>
      )}
      {controlState.outcome && (
        <p role="status">
          {controlState.outcome.accepted
            ? t("Control accepted.")
            : t("This execution did not accept the control.")}
        </p>
      )}
      {!guest && (child.available_actions?.length ?? 0) > 0 && (
        <form
          className={styles.controls}
          onSubmit={(event) => {
            event.preventDefault();
            if (hasMessage && !controlState.pending && !controlState.unknown)
              control.mutate("steer");
          }}
        >
          {child.available_actions?.includes("steer") && (
            <div
              className={`${conversation.composerInput} ${styles.steerComposer}`}
            >
              <label>
                <span className="sr-only">{t("Message to subagent")}</span>
                <Textarea
                  unstyled
                  rows={1}
                  aria-label={t("Message to subagent")}
                  placeholder={t("Send a message…")}
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter" &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      event.currentTarget.form?.requestSubmit();
                    }
                  }}
                  value={controlState.instruction}
                  disabled={controlState.pending || controlState.unknown}
                  onChange={(event) =>
                    updateControl({ instruction: event.target.value })
                  }
                />
              </label>
              <Button
                type={stopAction ? "button" : "submit"}
                onClick={() => {
                  if (stopAction) control.mutate("cancel");
                }}
                className={conversation.sendButton}
                aria-label={t(stopAction ? "Stop" : "Steer")}
                title={t(stopAction ? "Stop" : "Steer")}
                size="icon"
                disabled={
                  (!hasMessage && !stopAction) ||
                  controlState.pending ||
                  controlState.unknown
                }
              >
                {stopAction ? <StopIcon weight="fill" /> : <ArrowUp />}
              </Button>
            </div>
          )}
        </form>
      )}
    </>
  );
}
function ToolRecord({ item }: { item: BackgroundItem }) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const output = useQuery({
    queryKey: [
      "thread",
      item.target.threadId,
      "tool-executions",
      "output",
      item.target.id,
      item.state,
    ],
    queryFn: ({ signal }) =>
      result(
        client.GET(
          "/api/threads/{thread_id}/tool-executions/{execution_id}/output",
          {
            params: {
              path: {
                thread_id: item.target.threadId,
                execution_id: item.target.id,
              },
            },
            signal,
          },
        ),
      ),
    refetchInterval: item.running ? 2000 : false,
  });
  const input = item.call?.input ?? parsed(output.data?.input);
  const tool = { id: item.target.id, name: item.tool!.tool_name, input };
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
