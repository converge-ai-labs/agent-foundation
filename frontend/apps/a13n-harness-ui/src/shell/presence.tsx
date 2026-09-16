import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router";
import { useSources, useTransport } from "../transport/context";
import { watchSummary } from "../transport/events";
import { refreshThread, scheduleRefresh } from "../conversations/refresh";
import type { Schema } from "../transport/client";
import { useNotifications } from "./notifications";
import { useResults } from "../conversations/results";

export type Profile = { display_name: string; color: string };
export function pageFocus(
  path: string,
  source?: Schema<"ConfigurationSourceInfo">,
): Schema<"PageFocus"> {
  const parts = path.split("/").filter(Boolean);
  if (source?.resource_ids.length === 1) {
    const id = source.resource_ids[0];
    if (source.resource_kind === "project")
      return { target: { kind: "project", project_id: id } };
    const kinds = [
      "model",
      "agent",
      "subagent",
      "harness_plugin",
      "environment_profile",
      "environment_run_extension",
      "mcp_server",
      "content_plugin",
    ] as const;
    const kind = kinds.find((kind) => kind === source.resource_kind);
    if (kind)
      return {
        target: { kind: "resource", resource_kind: kind, resource_id: id },
      };
  }
  if (parts[0] === "threads" && parts[1])
    return {
      root_thread_id: parts[1],
      target: { kind: "conversation", thread_id: parts[1] },
    };
  if (parts[0] === "projects" && parts[1])
    return { target: { kind: "project", project_id: parts[1] } };
  return {
    target: {
      kind: "workbench",
      section:
        path === "/settings/catalog" || path === "/settings/capabilities"
          ? "catalog"
          : parts[0] === "settings" || parts[0] === "setup"
            ? "settings"
            : "home",
    },
  };
}

export function useLiveWorkbench(
  profile: Profile,
  enabled: boolean,
  unauthorized: () => void,
  nativeFocus: Schema<"PageTarget"> | null = null,
) {
  const transport = useTransport();
  const queries = useQueryClient();
  const notify = useNotifications()?.receive;
  const { tracker: results } = useResults();
  const location = useLocation();
  const sources = useSources();
  const sourcePath = new URLSearchParams(location.search).get("path");
  const focusedSource =
    location.pathname === "/settings/source"
      ? sources.data?.sources.find(
          (source) => source.relative_path === sourcePath,
        )
      : undefined;
  const [summary, setSummary] = useState("Connecting");
  const summarySubscription = useRef<ReturnType<typeof watchSummary> | null>(
    null,
  );
  const [presenceState, setPresenceState] = useState("Connecting");
  const [presence, setPresence] = useState<Schema<"PresenceFrame"> | null>(
    null,
  );
  const socket = useRef<WebSocket | null>(null);
  const report = useRef<Schema<"PresenceReport">>({});
  report.current = {
    kind: "presence",
    ...profile,
    focus: nativeFocus
      ? {
          target: nativeFocus,
          root_thread_id: location.pathname.startsWith("/threads/")
            ? location.pathname.split("/")[2]
            : null,
        }
      : pageFocus(location.pathname, focusedSource),
    foreground: document.visibilityState === "visible" && document.hasFocus(),
    pointer_enabled: true,
  };

  useEffect(() => {
    const close = watchSummary(
      transport,
      (event) => {
        if (event) notify?.(event);
        if (!event || event.kind === "configuration") results?.invalidate();
        else if (["thread", "root_operation"].includes(event.kind))
          results?.invalidate(
            event.root_thread_id ?? event.thread_id ?? undefined,
          );
        if (event?.kind === "comment")
          void queries.invalidateQueries({
            queryKey: event.root_thread_id
              ? ["comments", event.root_thread_id]
              : ["comments"],
          });
        else if (
          event &&
          ["thread", "root_operation", "child_execution"].includes(event.kind)
        ) {
          const id = event.root_thread_id ?? event.thread_id;
          if (id) {
            refreshThread(
              queries,
              id,
              event.kind === "child_execution"
                ? "children"
                : event.kind === "thread"
                  ? "checkpoint"
                  : "lifecycle",
            );
          } else
            scheduleRefresh(queries, (query) =>
              ["thread", "threads"].includes(String(query.queryKey[0])),
            );
        } else {
          // Open/reset reconcile all observations. Configuration changes do not
          // invalidate native files, and routine execution never reaches here.
          scheduleRefresh(
            queries,
            (query) => !event || query.queryKey[0] !== "native",
          );
        }
      },
      setSummary,
    );
    summarySubscription.current = close;
    return close;
  }, [transport, queries, notify, results]);

  useEffect(() => {
    if (summary === "Live") return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") {
        results?.invalidate();
        scheduleRefresh(queries, (query) =>
          ["thread", "threads"].includes(String(query.queryKey[0])),
        );
      }
    }, 15000);
    return () => clearInterval(timer);
  }, [summary, queries, results]);

  useEffect(() => {
    if (!enabled) {
      setPresenceState("Unavailable");
      return;
    }
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let heartbeat: ReturnType<typeof setInterval> | undefined;
    let stale: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const send = () => {
      report.current.foreground =
        document.visibilityState === "visible" && document.hasFocus();
      if (socket.current?.readyState === WebSocket.OPEN)
        socket.current.send(JSON.stringify(report.current));
    };
    function connect() {
      setPresenceState("Connecting");
      const url = new URL("/api/presence/connect", window.location.origin);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(url);
      socket.current = ws;
      ws.onopen = () => {
        ws.send(
          JSON.stringify({
            api_key: transport.key,
          } satisfies Schema<"InteractiveAuthentication">),
        );
        send();
        heartbeat = setInterval(send, 20000);
      };
      ws.onmessage = (event) => {
        try {
          const frame: unknown = JSON.parse(String(event.data));
          // Pointer delivery is consumed by its own lightweight overlay, not the workbench tree.
          if (
            typeof frame === "object" &&
            frame !== null &&
            "kind" in frame &&
            frame.kind === "pointers"
          )
            return;
          if (
            typeof frame !== "object" ||
            frame === null ||
            !("kind" in frame) ||
            frame.kind !== "presence" ||
            !("participants" in frame) ||
            !Array.isArray(frame.participants)
          )
            throw new Error("Invalid presence frame.");
          clearTimeout(stale);
          stale = setTimeout(() => {
            setPresence(null);
            setPresenceState("Reconnecting");
            ws.close(4000, "Presence updates timed out");
          }, 45000);
          setPresence(frame as Schema<"PresenceFrame">);
          setPresenceState("Live");
          failures = 0;
        } catch {
          ws.close(1002, "Invalid presence frame");
        }
      };
      ws.onclose = (event) => {
        clearInterval(heartbeat);
        clearTimeout(stale);
        setPresence(null);
        if (stopped) return;
        if (event.code === 4401) {
          unauthorized();
          return;
        }
        setPresenceState("Reconnecting");
        retry = setTimeout(connect, Math.min(1000 * 2 ** failures++, 15000));
      };
    }
    connect();
    document.addEventListener("visibilitychange", send);
    window.addEventListener("focus", send);
    window.addEventListener("blur", send);
    return () => {
      stopped = true;
      clearInterval(heartbeat);
      clearTimeout(stale);
      clearTimeout(retry);
      document.removeEventListener("visibilitychange", send);
      window.removeEventListener("focus", send);
      window.removeEventListener("blur", send);
      socket.current?.close();
      socket.current = null;
    };
  }, [transport, enabled, unauthorized]);

  useEffect(() => {
    if (socket.current?.readyState === WebSocket.OPEN)
      socket.current.send(JSON.stringify(report.current));
  }, [profile, location.pathname, location.search, focusedSource, nativeFocus]);
  return {
    summary,
    retrySummary: () => summarySubscription.current?.retry(),
    presenceState,
    presence,
    socket,
    focus: report.current.focus,
  };
}
