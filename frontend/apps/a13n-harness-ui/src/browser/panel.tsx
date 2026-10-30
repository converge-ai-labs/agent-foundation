import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  MenuItem,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuSub,
  MenuSubTrigger,
  MenuSubPopup,
  MenuRadioGroup,
  MenuRadioItem,
} from "a13n-ui";
import {
  ArrowClockwise,
  Devices,
  DotsThree,
  HandArrowDown,
  HandArrowUp,
  LockSimple,
  MagnifyingGlassPlus,
  Monitor,
  Pause,
  Play,
  X,
} from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { Desktop, type BrowserScale } from "./desktop";
import { BrowserSetup } from "./setup";
import styles from "./panel.module.css";

export function BrowserPanel({
  threadId,
  fullControl,
}: {
  threadId?: string;
  fullControl: boolean;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const [pending, setPending] = useState("");
  const busy = !!pending;
  const [scale, setScale] = useState<BrowserScale>("fit");
  const [now, setNow] = useState(() => Date.now());
  const lastRenewal = useRef(0);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const [error, setError] = useState("");
  const [authority, setAuthority] = useState<{
    threadId: string;
    binding: string;
    token: string;
  } | null>(null);
  const state = useQuery({
    queryKey: ["browser", threadId],
    enabled: !!threadId && fullControl && !busy,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/browser", {
          params: { path: { thread_id: threadId! } },
          signal,
        }),
      ),
    refetchInterval: 2000,
    staleTime: 2000,
    retry: false,
  });
  const session = state.data;
  const controlToken =
    authority &&
    authority.threadId === threadId &&
    authority.binding === session?.session_id
      ? authority.token
      : "";
  const preparation = useQuery({
    queryKey: ["browser-setup", threadId],
    enabled:
      !!threadId &&
      fullControl &&
      state.isSuccess &&
      session?.status === "failed",
    queryFn: () =>
      result(
        client.GET("/api/threads/{thread_id}/browser/setup", {
          params: { path: { thread_id: threadId! } },
        }),
      ),
    refetchOnWindowFocus: false,
    retry: false,
  });
  const refresh = () => queries.invalidateQueries({ queryKey: ["browser"] });
  const renewControl = () => {
    if (!threadId || !session?.session_id || !controlToken) return;
    const now = Date.now();
    if (now - lastRenewal.current < 10000) return;
    lastRenewal.current = now;
    void result(
      client.POST("/api/threads/{thread_id}/browser/renew-control", {
        params: { path: { thread_id: threadId } },
        body: { session_id: session.session_id, control_token: controlToken },
      }),
    ).then(
      () => refresh(),
      () => refresh(),
    );
  };
  const command = async (
    kind: "start" | "reset" | "close" | "take-control" | "handback",
  ) => {
    if (!threadId || busy) return;
    setPending(kind);
    setError("");
    await queries.cancelQueries({ queryKey: ["browser", threadId] });
    try {
      const path = {
        params: { path: { thread_id: threadId } },
        body: { session_id: session?.session_id ?? "" },
      };
      if (kind === "start") {
        const value = await result(
          client.POST("/api/threads/{thread_id}/browser/start", {
            params: path.params,
          }),
        );
        queries.setQueryData(["browser", threadId], value);
      }
      if (kind === "reset") {
        const value = await result(
          client.POST("/api/threads/{thread_id}/browser/reset", path),
        );
        queries.setQueryData(["browser", threadId], value);
        setAuthority(null);
      }
      if (kind === "close") {
        const value = await result(
          client.POST("/api/threads/{thread_id}/browser/close", path),
        );
        queries.setQueryData(["browser", threadId], value);
        setAuthority(null);
      }
      if (kind === "take-control") {
        const value = await result(
          client.POST("/api/threads/{thread_id}/browser/take-control", path),
        );
        queries.setQueryData(["browser", threadId], value.state);
        setAuthority({
          threadId,
          binding: value.state.session_id!,
          token: value.control_token,
        });
      }
      if (kind === "handback") {
        const value = await result(
          client.POST("/api/threads/{thread_id}/browser/handback", {
            ...path,
            body: {
              ...path.body,
              control_episode: session?.control_episode ?? 0,
            },
          }),
        );
        queries.setQueryData(["browser", threadId], value.state);
        setAuthority(null);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      if (kind === "start" || kind === "reset") void preparation.refetch();
      void refresh();
    } finally {
      setPending("");
    }
  };
  const changeViewport = async (
    preset: Schema<"BrowserViewportRequest">["preset"],
  ) => {
    if (!threadId || !session?.session_id || !controlToken || busy) return;
    setPending("viewport");
    setError("");
    await queries.cancelQueries({ queryKey: ["browser", threadId] });
    try {
      const value = await result(
        client.POST("/api/threads/{thread_id}/browser/viewport", {
          params: { path: { thread_id: threadId } },
          body: {
            session_id: session.session_id,
            control_token: controlToken,
            preset,
          },
        }),
      );
      queries.setQueryData(["browser", threadId], value);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      void refresh();
    } finally {
      setPending("");
    }
  };
  useEffect(() => {
    if (session?.control !== "human") setAuthority(null);
  }, [session?.control]);
  useEffect(() => {
    setAuthority(null);
  }, [threadId, session?.session_id]);
  if (!threadId)
    return (
      <div className={styles.empty}>
        <Monitor size={28} />
        <strong>Start a conversation first</strong>
        <p>
          Send a message to start this conversation. Your Agent can browse when
          needed.
        </p>
      </div>
    );
  if (!fullControl)
    return (
      <div className={styles.empty}>
        <LockSimple size={28} />
        <strong>Browser unavailable</strong>
        <p>Use a Full Control conversation.</p>
      </div>
    );
  const ready = session?.status === "ready" && !!session.page_count;
  const preparing = busy || state.isLoading || session?.status === "starting";
  const disabled = session?.status === "disabled";
  const needsHelp =
    !ready &&
    !busy &&
    !!(error || session?.error || session?.status === "failed");
  const human = session?.control === "human";
  const remaining = session?.control_remaining_seconds;
  const seconds =
    remaining == null
      ? null
      : Math.max(0, Math.ceil(remaining - (now - state.dataUpdatedAt) / 1000));
  const leaseLabel =
    seconds === null
      ? ""
      : seconds === 0
        ? "Returning control…"
        : `Auto handback in ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  const mac = session?.platform === "macos";
  const transfer = human && !mac && !controlToken;
  const handback = human && !transfer;
  const controlLabel = mac
    ? human
      ? "Resume automation"
      : "Pause automation"
    : transfer
      ? "Take control here"
      : human
        ? "Hand back to Agent"
        : "Take control";
  const canResize = !busy && !!controlToken && !!session?.resize_available;
  const viewport = session?.viewports?.find(
    (item) => item.preset === session.viewport,
  );
  const actions = ready ? (
    <>
      <Button
        size="sm"
        aria-label={controlLabel}
        variant={human ? "default" : "outline"}
        title={
          transfer
            ? "Move control to this browser while keeping Agent tools paused"
            : human
              ? "Resume browser tools in all conversations"
              : "Pause browser tools in all conversations"
        }
        disabled={busy}
        onClick={() => void command(handback ? "handback" : "take-control")}
      >
        {mac ? (
          human ? (
            <Play />
          ) : (
            <Pause />
          )
        ) : handback ? (
          <HandArrowUp />
        ) : (
          <HandArrowDown />
        )}
        {pending === "take-control"
          ? mac
            ? "Pausing…"
            : "Taking control…"
          : pending === "handback"
            ? "Handing back…"
            : handback && !mac
              ? "Hand back"
              : controlLabel}
      </Button>
      {transfer && (
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Hand back to Agent"
          title="Resume browser tools in all conversations"
          disabled={busy}
          onClick={() => void command("handback")}
        >
          <HandArrowUp />
        </Button>
      )}
      <Button
        size="icon-sm"
        variant="ghost"
        aria-label="Reset browser"
        title="Reset this conversation’s browser window"
        disabled={busy}
        onClick={() => void command("reset")}
      >
        <ArrowClockwise />
      </Button>
      <Menu>
        <MenuTrigger
          render={<Button size="icon-sm" variant="ghost" />}
          aria-label="Browser options"
          title="Browser options"
        >
          <DotsThree />
        </MenuTrigger>
        <MenuPopup align="end" className={styles.optionsMenu}>
          <MenuItem disabled={busy} onClick={() => void command("close")}>
            <X />
            Close conversation tabs
          </MenuItem>
          <MenuSub>
            <MenuSubTrigger>
              <MagnifyingGlassPlus />
              Zoom
              <span className={styles.menuValue}>
                {scale === "fit" ? "Fit" : `${scale}%`}
              </span>
            </MenuSubTrigger>
            <MenuSubPopup>
              <MenuRadioGroup
                value={scale}
                onValueChange={(value) => setScale(value as BrowserScale)}
              >
                <MenuRadioItem value="fit">Fit to panel</MenuRadioItem>
                {(["50", "75", "100", "125", "150", "200"] as const).map(
                  (value) => (
                    <MenuRadioItem key={value} value={value}>
                      {value}%
                    </MenuRadioItem>
                  ),
                )}
              </MenuRadioGroup>
            </MenuSubPopup>
          </MenuSub>
          <MenuSub>
            <MenuSubTrigger
              disabled={!canResize}
              title={
                canResize
                  ? "Resize the page"
                  : !session.resize_available
                    ? "Viewport resizing is unavailable on this Host"
                    : "Take control to resize the page"
              }
            >
              <Devices />
              Viewport
              <span className={styles.menuValue}>{viewport?.label}</span>
            </MenuSubTrigger>
            <MenuSubPopup>
              <MenuRadioGroup
                value={session.viewport}
                onValueChange={(value) =>
                  void changeViewport(
                    value as Schema<"BrowserViewportRequest">["preset"],
                  )
                }
              >
                {(session.viewports ?? []).map((item) => (
                  <MenuRadioItem key={item.preset} value={item.preset}>
                    {item.label} ({item.width} × {item.height})
                  </MenuRadioItem>
                ))}
              </MenuRadioGroup>
            </MenuSubPopup>
          </MenuSub>
        </MenuPopup>
      </Menu>
    </>
  ) : null;
  return (
    <section className={styles.panel} aria-label="Host browser">
      {!needsHelp && (error || state.error || session?.error) && (
        <p className={styles.notice} role="alert">
          {error || state.error?.message || session?.error}
        </p>
      )}
      {ready ? (
        <>
          {human && leaseLabel && (
            <p className={styles.notice} role="status">
              {leaseLabel}
            </p>
          )}
          {session.platform === "macos" && (
            <p className={styles.notice}>
              {human
                ? "Automation paused. Native Mac input does not renew the timer; resume here when finished."
                : "Read-only window preview. Pause automation before operating this browser on the Mac."}
            </p>
          )}
          <Desktop
            key={session.session_id}
            threadId={threadId}
            session={session}
            controlToken={human ? controlToken : ""}
            busy={busy}
            activity={
              pending === "take-control"
                ? "Taking control…"
                : pending === "handback"
                  ? "Handing back…"
                  : busy
                    ? "Updating browser…"
                    : ""
            }
            onReconnectControl={() => void command("take-control")}
            onControlActivity={renewControl}
            scale={scale}
            actions={actions}
          />
        </>
      ) : human && session?.session_id ? (
        <div className={styles.empty}>
          <Pause size={28} />
          <strong>Browser automation paused</strong>
          <p>Hand back to resume browser tools in all conversations.</p>
          <Button
            aria-label={mac ? "Resume automation" : "Hand back to Agent"}
            disabled={busy}
            onClick={() => void command("handback")}
          >
            {mac ? <Play /> : <HandArrowUp />}
            {pending === "handback"
              ? "Handing back…"
              : mac
                ? "Resume automation"
                : "Hand back"}
          </Button>
        </div>
      ) : needsHelp ? (
        <BrowserSetup
          setup={preparation.data}
          loading={preparation.isFetching}
          error={error || session?.error || preparation.error?.message || ""}
          retry={() => void command("start")}
        />
      ) : state.isError ? (
        <div className={styles.empty}>
          <Button variant="outline" onClick={() => void state.refetch()}>
            Try again
          </Button>
        </div>
      ) : (
        <div className={styles.empty}>
          <Monitor size={28} />
          <strong>
            {preparing
              ? "Preparing browser…"
              : disabled
                ? "Browser disabled"
                : "Ready when you need it"}
          </strong>
          <p>
            {preparing
              ? "Preparing the shared Host browser."
              : disabled
                ? "Enable automatic browsing in browser settings."
                : "Your Agent opens pages as needed. You can also open a window to sign in. Login sessions are shared across conversations."}
          </p>
          {disabled ? (
            <a href="/settings/resources#browser">Browser settings</a>
          ) : (
            !preparing && (
              <Button
                variant="outline"
                disabled={!state.isSuccess}
                onClick={() => void command("start")}
              >
                <Monitor /> Open browser
              </Button>
            )
          )}
        </div>
      )}
    </section>
  );
}
