import conversation from "./conversation.module.css";
import { ArrowUp, Stop as StopIcon } from "@phosphor-icons/react";
import { ChildInputLabels } from "./input-content";
import { useEffect, useLayoutEffect, useRef } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
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
import { sourceText, parsed, record } from "./tool-presentation";
import styles from "./background.module.css";

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
        <h2 title={target.title}>{target.title}</h2>
        <span>{item ? state(item.state) : t("Unavailable")}</span>
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
  const { client } = useTransport();
  const guest = useGuestAccess();
  const bg = useBackground();
  const child = item.child!;
  const live = bg.display?.childOutput(child);
  const checkpoint = live ? [...live.checkpoints.keys()].at(-1) : undefined;
  const history = useInfiniteQuery({
    queryKey: [
      "thread",
      child.parent_thread_id,
      "child-review",
      child.child_thread_id,
      "transcript",
      checkpoint ?? child.updated_at,
    ],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ signal, pageParam }) =>
      result(
        client.GET("/api/threads/{thread_id}/transcript", {
          params: {
            path: { thread_id: child.child_thread_id },
            query: { limit: 100, cursor: pageParam },
          },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    placeholderData: (previous) => previous,
  });
  const source = history.data?.pages[0]?.continuation_id;
  const entries = [
    ...new Map(
      history.data?.pages
        .flatMap((page) => [...page.entries, ...(page.boundary_entries ?? [])])
        .map((entry) => [entry.position, entry]) ?? [],
    ).values(),
  ].sort((a, b) => a.position - b.position);
  const turns = [
    ...new Map(
      history.data?.pages
        .flatMap((page) => page.turns ?? [])
        .map((turn) => [turn.turn_id, turn]) ?? [],
    ).values(),
  ];
  const { controlState, updateControl, control } = useChildControl(
    child,
    bg.refresh,
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
        {history.isPending && (
          <p role="status">{t("Loading subagent conversation…")}</p>
        )}
        {history.hasNextPage && (
          <Button
            variant="ghost"
            size="sm"
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
            blocks={live?.blocksAfter(source) ?? []}
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
            if (
              controlState.instruction.trim() &&
              !controlState.pending &&
              !controlState.unknown
            )
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
                type="submit"
                className={conversation.sendButton}
                aria-label={t("Steer")}
                title={t("Steer")}
                size="icon"
                disabled={
                  !controlState.instruction.trim() ||
                  controlState.pending ||
                  controlState.unknown
                }
              >
                <ArrowUp />
              </Button>
            </div>
          )}
          {child.available_actions?.includes("cancel") && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={controlState.pending || controlState.unknown}
              onClick={() => control.mutate("cancel")}
            >
              <StopIcon />
              {t("Stop")}
            </Button>
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
  useEffect(() => {
    void output.refetch();
  }, [item.state]);
  const value = parsed(output.data?.text);
  const text =
    record(value) && typeof value.stdout === "string"
      ? [value.stdout, value.stderr]
          .filter((text) => typeof text === "string" && text)
          .join("\n")
      : sourceText(value);
  return (
    <div className={`${styles.reader} a13n-scrollbar`}>
      <ToolCancel item={item} />
      <h3>{t("Input")}</h3>
      <pre>
        {item.call
          ? sourceText(item.call.input)
          : output.data?.input != null
            ? sourceText(parsed(output.data.input))
            : t("Input unavailable")}
      </pre>
      {output.data?.input_truncated && <p>{t("Input truncated.")}</p>}
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
      {output.data?.text != null && <pre>{text}</pre>}
    </div>
  );
}
