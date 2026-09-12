import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router";
import { useSources, useTransport } from "../transport/context";
import { watchSummary } from "../transport/events";
import type { Schema } from "../transport/client";

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
        path === "/settings/catalog"
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
) {
  const transport = useTransport();
  const queries = useQueryClient();
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
  const [presenceState, setPresenceState] = useState("Connecting");
  const [presence, setPresence] = useState<Schema<"PresenceFrame"> | null>(
    null,
  );
  const socket = useRef<WebSocket | null>(null);
  const report = useRef<Schema<"PresenceReport">>({});
  report.current = {
    kind: "presence",
    ...profile,
    focus: pageFocus(location.pathname, focusedSource),
    foreground: document.visibilityState === "visible",
  };

  useEffect(
    () =>
      watchSummary(
        transport,
        () => {
          void queries.invalidateQueries();
        },
        setSummary,
      ),
    [transport, queries],
  );

  useEffect(() => {
    if (!enabled) {
      setPresenceState("Unavailable");
      return;
    }
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let heartbeat: ReturnType<typeof setInterval> | undefined;
    let failures = 0;
    const send = () => {
      report.current.foreground = document.visibilityState === "visible";
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
          if (
            typeof frame !== "object" ||
            frame === null ||
            !("kind" in frame) ||
            frame.kind !== "presence" ||
            !("participants" in frame) ||
            !Array.isArray(frame.participants)
          )
            throw new Error("Invalid presence frame.");
          setPresence(frame as Schema<"PresenceFrame">);
          setPresenceState("Live");
          failures = 0;
        } catch {
          ws.close(1002, "Invalid presence frame");
        }
      };
      ws.onclose = (event) => {
        clearInterval(heartbeat);
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
    return () => {
      stopped = true;
      clearInterval(heartbeat);
      clearTimeout(retry);
      document.removeEventListener("visibilitychange", send);
      socket.current?.close();
      socket.current = null;
    };
  }, [transport, enabled, unauthorized]);

  useEffect(() => {
    if (socket.current?.readyState === WebSocket.OPEN)
      socket.current.send(JSON.stringify(report.current));
  }, [profile, location.pathname, location.search, focusedSource]);
  return { summary, presenceState, presence };
}
