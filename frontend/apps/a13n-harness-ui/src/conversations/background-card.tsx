import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
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
import { useMutation, useQuery } from "@tanstack/react-query";
import { result } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { useChildControl } from "./child-controls";
import { useBackground } from "./background-context";
import {
  backgroundGroups,
  backgroundKey,
  glyphIdentity,
  type BackgroundItem,
  argumentPreview,
} from "./background-work";
import { parsed, record } from "./tool-presentation";
import styles from "./background.module.css";

export function SubagentGlyph({
  id,
  running = false,
}: {
  id: string;
  running?: boolean;
}) {
  const identity = glyphIdentity(id);
  return (
    <span
      aria-hidden="true"
      className={styles.glyph}
      data-shape={identity.shape}
      data-running={running || undefined}
      style={
        {
          "--identity": `var(--a13n-identity-${identity.color + 1})`,
        } as CSSProperties
      }
    />
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
  const trigger = useRef<HTMLButtonElement>(null);
  const measure = () => {
    const rect = trigger.current?.getBoundingClientRect();
    if (rect) bg.setAnchor?.({ right: rect.right, bottom: rect.bottom });
  };
  useLayoutEffect(measure);
  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => {
      window.removeEventListener("resize", measure);
    };
  }, []);
  const count = bg.items.filter((item) => item.running).length;
  return (
    <Button
      variant="ghost"
      size="sm"
      ref={trigger}
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
  const [limit, setLimit] = useState(20);
  const groups = backgroundGroups(bg.items, limit);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!bg.cardOpen) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [bg.cardOpen]);
  if (!bg.cardOpen) return null;
  const composer = document.querySelector<HTMLElement>(
    "[data-composer-editor]",
  );
  const composerTop = composer?.getBoundingClientRect().top;
  const bottom =
    composerTop && composerTop > (bg.anchor?.bottom ?? 48)
      ? composerTop - 16
      : window.innerHeight - 16;
  const top = (bg.anchor?.bottom ?? 40) + 8;
  const right =
    window.innerWidth <= 600
      ? 8
      : Math.max(
          8,
          window.innerWidth - (bg.anchor?.right ?? window.innerWidth),
        );
  const bodyHeight = Math.max(80, Math.min(480, bottom - top - 84));

  return (
    <section
      style={
        {
          top,
          right,
          "--background-body-height": `${bodyHeight}px`,
        } as CSSProperties
      }
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
      <ScrollArea
        autoHeight
        scrollFade
        className={styles.cardBody}
        viewportProps={{ "aria-label": t("Background items"), role: "region" }}
      >
        <ErrorNotice error={bg.error} retry={bg.refresh} />
        {(bg.display?.gap || bg.display?.processes.omitted) && (
          <p className={styles.empty}>
            {t("Some process observations are unavailable or omitted.")}
          </p>
        )}
        {bg.loading && <p role="status">{t("Loading background work…")}</p>}
        {!bg.loading && !bg.error && !groups.running.length && (
          <p className={styles.empty}>{t("Nothing is running.")}</p>
        )}
        {(
          [
            ["Running", groups.running],
            ["Ended", groups.ended],
          ] as const
        ).map(
          ([label, items]) =>
            items.length > 0 && (
              <section key={label} aria-label={t(label)}>
                <h3>{t(label)}</h3>
                <ul>
                  {items.map((item) => (
                    <BackgroundRow
                      key={backgroundKey(item.target)}
                      item={item}
                      now={now}
                    />
                  ))}
                </ul>
              </section>
            ),
        )}
      </ScrollArea>
      {groups.more && (
        <Button
          className={styles.showMore}
          variant="ghost"
          size="sm"
          onClick={() => setLimit(limit + 20)}
        >
          {t("Show more")}
        </Button>
      )}
    </section>
  );
}
function BackgroundRow({ item, now }: { item: BackgroundItem; now: number }) {
  const { t } = useTranslation();
  const bg = useBackground();
  const state = useBackgroundStateLabel();
  const elapsed =
    item.started && (item.running || item.ended)
      ? Math.max(
          0,
          Math.floor(
            ((item.ended ? Date.parse(item.ended) : now) -
              Date.parse(item.started)) /
              1000,
          ),
        )
      : undefined;
  const duration =
    elapsed === undefined
      ? ""
      : elapsed === 0
        ? t("<1s")
        : elapsed >= 60
          ? t("{{minutes}}m {{seconds}}s", {
              minutes: Math.floor(elapsed / 60),
              seconds: elapsed % 60,
            })
          : t("{{seconds}}s", { seconds: elapsed });
  const { client } = useTransport();
  const output = useQuery({
    queryKey: [
      "thread",
      item.target.threadId,
      "tool-executions",
      "output",
      item.target.id,
    ],
    enabled: !!item.tool,
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
  });
  const outputHint =
    output.data?.state === "expired"
      ? t("Output expired")
      : output.data?.truncated
        ? t("Output truncated")
        : "";
  const shell = item.process || item.tool?.tool_name === "shell_exec";
  const input = parsed(item.call?.input ?? output.data?.input);
  const command =
    record(input) && typeof input.command === "string"
      ? input.command
      : item.process?.command;
  const name = shell ? command || item.target.title : item.target.title;
  const preview = item.child
    ? ""
    : shell
      ? ""
      : argumentPreview(input) || item.preview;

  return (
    <li className={styles.row}>
      <button
        type="button"
        className={styles.item}
        onClick={() => bg.open({ ...item.target, title: name })}
        title={command || preview || name}
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
        <span className={styles.copy}>
          <span
            className={`${styles.name} ${shell ? styles.command : ""}`}
            title={name}
          >
            {name}
          </span>
          <span className={styles.preview}>
            {outputHint && (
              <>
                {outputHint}
                {!shell && preview ? " · " : ""}
              </>
            )}
            {item.child ? (
              <ChildPrompt item={item} />
            ) : shell ? (
              "\u00a0"
            ) : (
              preview || (outputHint ? "" : t("Input unavailable"))
            )}
          </span>
        </span>
        <span className={styles.metadata}>
          <span>
            {state(item.state)}
            {duration && (
              <>
                {" "}
                · <time>{duration}</time>
              </>
            )}
          </span>
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
  if (guest || !item.child?.available_actions?.includes("cancel")) return null;
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={t("Stop subagent")}
      title={t("Stop subagent")}
      disabled={controlState.pending || controlState.unknown}
      onClick={() => {
        control.mutate("cancel");
      }}
    >
      <StopIcon />
    </Button>
  );
}
export function ToolCancel({ item }: { item: BackgroundItem }) {
  const { t } = useTranslation();
  const guest = useGuestAccess();
  const { client } = useTransport();
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
    onSettled: bg.refresh,
  });
  if (guest || !item.tool?.available_actions?.includes("cancel")) return null;
  return (
    <>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={t("Cancel execution")}
        title={t("Cancel execution")}
        disabled={cancel.isPending || cancel.isSuccess || cancel.isError}
        onClick={() => cancel.mutate()}
      >
        <X />
      </Button>
      {cancel.isError && (
        <span title={String(cancel.error)} role="alert">
          {t("Unconfirmed")}
        </span>
      )}
    </>
  );
}

function ChildPrompt({ item }: { item: BackgroundItem }) {
  const { client } = useTransport();
  const { t } = useTranslation();
  const prompt = useQuery({
    queryKey: [
      "thread",
      item.target.threadId,
      "child-review",
      item.target.id,
      "prompt",
    ],
    enabled: !item.preview,
    staleTime: Infinity,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/inputs", {
          params: { path: { thread_id: item.target.id }, query: { limit: 1 } },
          signal,
        }),
      ),
  });
  const text =
    item.preview || prompt.data?.turns[0]?.preview || t("Input unavailable");
  return <span title={text}>{text}</span>;
}
