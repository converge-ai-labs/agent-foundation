import { useEffect, useState, type CSSProperties } from "react";
import { Button, ScrollArea } from "a13n-ui";
import {
  CirclesThree,
  TerminalWindow,
  Wrench,
  X,
  Stop as StopIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { useGuestAccess, useTransport } from "../transport/context";
import {
  useMutation,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { useChildControl } from "./child-controls";
import { useBackground } from "./background-context";
import {
  backgroundGroups,
  backgroundKey,
  glyphIdentity,
  type BackgroundItem,
} from "./background-work";
import { executionTime } from "./execution-time";
import styles from "./background.module.css";

const bodies = [
  "M6 4Q12 1 18 4Q22 6 21 14L20 20Q17 23 14 20L12 22L10 20Q6 23 4 19L3 12Q2 6 6 4Z",
  "M12 3C23 3 24 18 18 21Q15 23 12 21Q8 23 5 20C0 14 2 3 12 3Z",
  "M7 4Q12 0 17 4L21 11Q24 17 18 20L14 22Q12 23 10 21L5 20Q0 17 3 11Z",
  "M5 4Q12 2 19 4Q22 5 22 9L21 18Q21 22 17 22L13 20L9 22Q4 23 3 18L2 10Q1 5 5 4Z",
];
const eyes = [
  "M7 10a1.4 1.8 0 1 0 2.8 0a1.4 1.8 0 1 0-2.8 0M14.2 10a1.4 1.8 0 1 0 2.8 0a1.4 1.8 0 1 0-2.8 0",
  "M6.5 10a2 2 0 1 0 4 0a2 2 0 1 0-4 0M13.5 10a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
  "M6.5 11Q8.5 6.5 10.5 11L9.3 11Q8.5 9 7.7 11ZM13.5 11Q15.5 6.5 17.5 11L16.3 11Q15.5 9 14.7 11Z",
];
const mouths = [
  "M10 15Q12 17 14 15",
  "M10.5 15.5h3",
  "M11 15a1.2 1.2 0 1 0 2 0",
];
const extras = [
  "",
  "M5 6L4 1L9 5M15 5L20 1L19 7",
  "M10 4L10 1L14 1L14 4",
  "M3 12L0 10L2 16M21 12L24 10L22 16",
];

export function SubagentGlyph({
  id,
  running = false,
}: {
  id: string;
  running?: boolean;
}) {
  const identity = glyphIdentity(id);
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className={styles.glyph}
      data-running={running || undefined}
      style={
        {
          "--identity": `var(--a13n-identity-${identity.color + 1})`,
        } as CSSProperties
      }
    >
      <g
        fill="var(--identity)"
        stroke="var(--a13n-identity-outline)"
        strokeWidth="0.6"
        strokeLinejoin="round"
      >
        {extras[identity.extra] && <path d={extras[identity.extra]} />}
        <path d={bodies[identity.shape]} />
      </g>
      <path d={eyes[identity.eyes]} fill="var(--a13n-identity-ink)" />
      <path
        d={mouths[identity.mouth]}
        fill="none"
        stroke="var(--a13n-identity-ink)"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </svg>
  );
}
export function useBackgroundStateLabel() {
  const { t } = useTranslation();
  const labels: Record<string, string> = {
    running: t("Running"),
    queued: t("Queued"),
    succeeded: t("Succeeded"),
    failed: t("Failed"),
    cancelled: t("Cancelled"),
    lost: t("Lost"),
    unavailable: t("Unavailable"),
    exited: t("Exited"),
    signaled: t("Signaled"),
    timed_out: t("Timed out"),
  };
  return (state: string) => labels[state] ?? state;
}
export function BackgroundToggle() {
  const { t } = useTranslation();
  const bg = useBackground();
  const count = bg.items.filter((item) => item.running).length;
  return (
    <Button
      variant="ghost"
      size="sm"
      aria-label={t("Background work")}
      title={t("Background work")}
      aria-expanded={bg.cardOpen}
      aria-controls="background-work-card"
      className={styles.toggle}
      onClick={() => bg.setCardOpen(!bg.cardOpen)}
    >
      <CirclesThree />
      {count > 0 && <span>{count}</span>}
    </Button>
  );
}
export function BackgroundCard() {
  const { t } = useTranslation();
  const bg = useBackground();
  if (!bg.cardOpen) return null;
  return (
    <section
      id="background-work-card"
      className={styles.card}
      aria-label={t("Background work")}
      onKeyDown={(event) => {
        if (event.key === "Escape") bg.setCardOpen(false);
      }}
    >
      <header>
        <h2>{t("Background work")}</h2>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("Close background work")}
          onClick={() => bg.setCardOpen(false)}
        >
          <X />
        </Button>
      </header>
      <BackgroundList compact />
    </section>
  );
}
export function BackgroundList({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation();
  const bg = useBackground();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <ScrollArea
      autoHeight={compact}
      scrollFade
      className={compact ? styles.cardBody : styles.historyBody}
      viewportProps={{ "aria-label": t("Background items"), role: "region" }}
    >
      <ErrorNotice error={bg.error} retry={bg.refresh} />
      {bg.display?.processes.omitted && (
        <p className={styles.empty}>
          {t("Some process observations are unavailable or omitted.")}
        </p>
      )}
      {bg.loading && (
        <p role="status" className={styles.empty}>
          {t("Loading background work…")}
        </p>
      )}
      {!bg.loading && !bg.error && !bg.items.length && (
        <p className={styles.empty}>{t("Nothing is running.")}</p>
      )}
      {backgroundGroups(bg.items).map(
        (group) =>
          group.items.length > 0 && (
            <section key={group.label} aria-label={t(group.label)}>
              <div className={styles.groupHeader}>
                <h3>{t(group.label)}</h3>
                {compact && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      bg.open({
                        kind: "history",
                        id: bg.threadId,
                        threadId: bg.threadId,
                        title: t("Background work"),
                      })
                    }
                  >
                    {t("View all")}
                  </Button>
                )}
              </div>
              <ul>
                {(compact ? group.items.slice(0, 3) : group.items).map(
                  (item) => (
                    <BackgroundRow
                      key={backgroundKey(item.target)}
                      item={item}
                      now={now}
                    />
                  ),
                )}
              </ul>
            </section>
          ),
      )}
      {!compact && bg.more && (
        <Button
          className={styles.showMore}
          variant="ghost"
          size="sm"
          loading={bg.loadingMore}
          onClick={bg.loadMore}
        >
          {t("Show more")}
        </Button>
      )}
    </ScrollArea>
  );
}
function BackgroundRow({ item, now }: { item: BackgroundItem; now: number }) {
  const bg = useBackground();
  const state = useBackgroundStateLabel();
  const duration =
    item.started && (item.running || item.ended)
      ? executionTime(
          (item.ended ? Date.parse(item.ended) : now) -
            Date.parse(item.started),
        )
      : undefined;
  const shell = item.process || item.target.icon === "terminal";
  return (
    <li className={styles.row}>
      <button
        type="button"
        className={styles.item}
        onClick={() => bg.open(item.target)}
        title={[item.target.title, item.preview].filter(Boolean).join("\n")}
      >
        <span className={styles.icon}>
          {item.child ? (
            <SubagentGlyph
              id={item.child.child_thread_id}
              running={item.running}
            />
          ) : shell ? (
            <TerminalWindow />
          ) : (
            <Wrench />
          )}
        </span>
        <span className={`${styles.name} ${shell ? styles.command : ""}`}>
          {item.target.title}
        </span>
        <span className={styles.metadata}>
          {state(item.state)}
          {duration && (
            <>
              {" "}
              · <time>{duration}</time>
            </>
          )}
        </span>
      </button>
      <span className={styles.action}>
        {item.child ? (
          <ChildStop item={item} />
        ) : item.tool ? (
          <ToolCancel item={item} />
        ) : null}
      </span>
    </li>
  );
}
export function ChildStop({ item }: { item: BackgroundItem }) {
  const { t } = useTranslation();
  const guest = useGuestAccess();
  const bg = useBackground();
  const { controlState, control } = useChildControl(item.child!, bg.refresh);
  if (
    !item.running ||
    guest ||
    !item.child?.available_actions?.includes("cancel")
  )
    return null;
  const stopping = controlState.pending || !!controlState.outcome?.accepted;
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      loading={stopping}
      aria-label={t(stopping ? "Stopping…" : "Stop subagent")}
      title={t(stopping ? "Stopping…" : "Stop subagent")}
      disabled={stopping || controlState.unknown}
      onClick={() => control.mutate()}
    >
      <StopIcon weight="fill" />
    </Button>
  );
}
export function ToolCancel({ item }: { item: BackgroundItem }) {
  const { t } = useTranslation();
  const guest = useGuestAccess();
  const { client } = useTransport();
  const queries = useQueryClient();
  const bg = useBackground();
  const cancel = useMutation({
    mutationFn: () =>
      result(
        client.POST(
          "/api/threads/{thread_id}/tool-executions/{execution_id}/cancel",
          {
            params: {
              path: {
                thread_id: item.target.threadId,
                execution_id: item.target.id,
              },
            },
          },
        ),
      ),
    onSuccess: (execution) =>
      queries.setQueryData<InfiniteData<Schema<"ToolExecutionView">[]>>(
        ["thread", item.target.threadId, "tool-executions", "background"],
        (current) =>
          current && {
            ...current,
            pages: current.pages.map((page) =>
              page.map((tool) =>
                tool.execution_id === execution.execution_id ? execution : tool,
              ),
            ),
          },
      ),
    onSettled: bg.refresh,
  });
  if (!item.running || guest) return null;
  const stopping = cancel.isPending || item.tool!.cancel_requested;
  if (!stopping && !item.tool?.available_actions?.includes("cancel"))
    return null;
  return (
    <>
      <Button
        variant="ghost"
        size="icon-sm"
        loading={stopping}
        aria-label={t(stopping ? "Stopping…" : "Cancel execution")}
        title={t(stopping ? "Stopping…" : "Cancel execution")}
        disabled={stopping || cancel.isError}
        onClick={() => cancel.mutate()}
      >
        <StopIcon weight="fill" />
      </Button>
      {cancel.isError && (
        <span title={String(cancel.error)} role="alert">
          {t("Unconfirmed")}
        </span>
      )}
    </>
  );
}
