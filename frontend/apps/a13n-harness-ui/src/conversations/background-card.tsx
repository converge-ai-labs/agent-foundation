import { BackgroundStateIcon } from "./background-state-icon";
import { useEffect, useState, type CSSProperties } from "react";
import { Button, ScrollArea } from "a13n-ui";
import {
  CaretRight,
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
  sectionTarget,
  SECTION_LABELS,
  type BackgroundItem,
  type BackgroundSection,
} from "./background-work";
import { executionTime } from "./execution-time";
import styles from "./background.module.css";

function GlyphEye({
  kind,
  x,
  y,
  gaze: [gx, gy],
}: {
  kind: number;
  x: number;
  y: number;
  gaze: readonly [number, number];
}) {
  const ink = "var(--a13n-identity-ink)";
  const light = "var(--a13n-identity-light)";
  const ex = x + gx;
  const ey = y + gy;
  if (kind === 0)
    return (
      <>
        <circle cx={ex} cy={ey} r="1.9" fill={ink} />
        <circle cx={ex + 0.6} cy={ey - 0.65} r="0.62" fill={light} />
      </>
    );
  if (kind === 1)
    return (
      <>
        <ellipse cx={ex} cy={ey} rx="1.45" ry="2.25" fill={ink} />
        <circle cx={ex + 0.45} cy={ey - 0.95} r="0.55" fill={light} />
      </>
    );
  if (kind === 2)
    return (
      <>
        <circle cx={x} cy={y} r="2.35" fill={light} />
        <circle cx={x + gx * 1.1} cy={y + gy * 1.4} r="1.25" fill={ink} />
      </>
    );
  return (
    <path
      d={`M${ex - 1.6} ${ey + 0.6}Q${ex} ${ey - 1.5} ${ex + 1.6} ${ey + 0.6}`}
      fill="none"
      stroke={ink}
      strokeWidth="1.35"
      strokeLinecap="round"
    />
  );
}

export function SubagentGlyph({
  id,
  running = false,
}: {
  id: string;
  running?: boolean;
}) {
  const glyph = glyphIdentity(id);
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className={styles.glyph}
      data-running={running || undefined}
      style={
        {
          "--identity": `var(--a13n-identity-${glyph.color + 1})`,
        } as CSSProperties
      }
    >
      <path d={glyph.path} fill="var(--identity)" />
      <g transform={`rotate(${glyph.tilt} ${glyph.eyeX} ${glyph.eyeY})`}>
        {[-3.2, 3.2].map((offset) => (
          <GlyphEye
            key={offset}
            kind={glyph.eyes}
            x={glyph.eyeX + offset}
            y={glyph.eyeY}
            gaze={glyph.gaze}
          />
        ))}
      </g>
    </svg>
  );
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
      <BackgroundList />
    </section>
  );
}
export function BackgroundList({ section }: { section?: BackgroundSection }) {
  const { t } = useTranslation();
  const bg = useBackground();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  // The card previews both sections; a section tab lists one complete history.
  const compact = !section;
  const groups = backgroundGroups(bg.items).filter(
    (group) => group.items.length && (compact || group.section === section),
  );
  return (
    <ScrollArea
      autoHeight={compact}
      scrollFade
      className={compact ? styles.cardBody : styles.historyBody}
      viewportProps={{ "aria-label": t("Background items"), role: "region" }}
    >
      <ErrorNotice error={bg.error} retry={bg.refresh} />
      {bg.display?.processes.omitted && section !== "subagents" && (
        <p className={styles.empty}>
          {t("Some process observations are unavailable or omitted.")}
        </p>
      )}
      {bg.loading && (
        <p role="status" className={styles.empty}>
          {t("Loading background work…")}
        </p>
      )}
      {!bg.loading && !bg.error && !groups.length && (
        <p className={styles.empty}>
          {t(compact ? "Nothing is running." : "Nothing has run yet.")}
        </p>
      )}
      {groups.map((group) => (
        <section
          key={group.section}
          aria-label={t(SECTION_LABELS[group.section])}
        >
          {compact && (
            <div className={styles.groupHeader}>
              <h3>{t(SECTION_LABELS[group.section])}</h3>
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  bg.open(sectionTarget(group.section, bg.threadId, t))
                }
              >
                {t("View all")}
              </Button>
            </div>
          )}
          {compact ? (
            <BackgroundRows items={group.items.slice(0, 3)} now={now} />
          ) : (
            <BackgroundHistory
              items={group.items}
              section={group.section}
              now={now}
            />
          )}
        </section>
      ))}
    </ScrollArea>
  );
}
function BackgroundRows({
  items,
  now,
}: {
  items: BackgroundItem[];
  now: number;
}) {
  return (
    <ul>
      {items.map((item) => (
        <BackgroundRow key={backgroundKey(item.target)} item={item} now={now} />
      ))}
    </ul>
  );
}

function BackgroundHistory({
  items,
  section,
  now,
}: {
  items: BackgroundItem[];
  section: BackgroundSection;
  now: number;
}) {
  const { t } = useTranslation();
  const bg = useBackground();
  const [expanded, setExpanded] = useState(false);
  const finished = items.filter(
    (item) => !item.running && item.state !== "unavailable",
  );
  const active = items.filter(
    (item) => item.running || item.state === "unavailable",
  );
  const more = bg.more[section];
  return (
    <>
      <BackgroundRows items={active} now={now} />
      {(finished.length > 0 || more) && (
        <>
          <button
            type="button"
            className={styles.finishedToggle}
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            <CaretRight aria-hidden="true" size={12} />
            {t("Finished")}
            {finished.length > 0 && (
              <span>
                {finished.length}
                {more ? "+" : ""}
              </span>
            )}
          </button>
          {expanded && (
            <>
              <BackgroundRows items={finished} now={now} />
              {more && (
                <Button
                  className={styles.showMore}
                  variant="ghost"
                  size="sm"
                  loading={bg.loadingMore[section]}
                  onClick={() => bg.loadMore(section)}
                >
                  {t("Show more")}
                </Button>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
function BackgroundRow({ item, now }: { item: BackgroundItem; now: number }) {
  const bg = useBackground();
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
        <span
          className={`${styles.name} ${shell ? styles.command : ""}`}
          title={[item.target.title, item.preview].filter(Boolean).join("\n")}
        >
          {item.target.title}
        </span>
        <span className={styles.metadata}>
          <BackgroundStateIcon state={item.state} />
          {duration && <time>{duration}</time>}
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
