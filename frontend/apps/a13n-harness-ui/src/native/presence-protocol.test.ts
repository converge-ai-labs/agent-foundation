import { afterAll, beforeAll, expect, it, vi } from "vitest";
import { startApp } from "../../tests/app-fixture";
import { createTransport, result, type Transport } from "../transport/client";
import { joinPath } from "./buffer";

let app: Awaited<ReturnType<typeof startApp>>;
let transport: Transport;
beforeAll(async () => {
  app = await startApp("--native");
  vi.stubGlobal("window", { location: { origin: app.origin } });
  transport = createTransport("test-only-key", () => {});
}, 40000);
afterAll(async () => {
  transport?.close();
  await app?.close();
  vi.unstubAllGlobals();
}, 20000);
const wait = (assertion: () => void) =>
  vi.waitFor(assertion, { timeout: 10000, interval: 30 });

it("real per-tab presence distinguishes native and conversation pages, background attention and deliberate peer links", async () => {
  const thread = await result(
    transport.client.POST("/api/threads", { body: {} }),
  );
  const { pageLink, nativeLink } = await import("../shell/page-links");
  const path = joinPath(app.native_root, "sample.txt");
  const peers = [true, false].map((foreground) => {
    const state: {
      frame: import("../transport/client").Schema<"PresenceFrame"> | null;
    } = { frame: null };
    const socket = new WebSocket(
      app.origin.replace("http:", "ws:") + "/api/presence/connect",
    );
    const report = (native: boolean) =>
      socket.send(
        JSON.stringify({
          kind: "presence",
          display_name: "Same name",
          color: "#64748b",
          foreground,
          focus: {
            root_thread_id: thread.thread_id,
            target: native
              ? { kind: "file", path }
              : { kind: "conversation", thread_id: thread.thread_id },
          },
        }),
      );
    socket.onopen = () => {
      socket.send(JSON.stringify({ api_key: "test-only-key" }));
      report(foreground);
    };
    socket.onmessage = (event) => {
      state.frame = JSON.parse(String(event.data));
    };
    return { socket, state, report };
  });
  try {
    await wait(() =>
      expect(
        peers[0].state.frame?.participants.filter((peer) => peer.focus),
      ).toHaveLength(2),
    );
    const a = peers[0].state.frame!;
    expect(
      new Set(a.participants.map((peer) => peer.participant_id)).size,
    ).toBe(2);
    expect(a.same_page_participant_ids).toEqual([]);
    expect(a.participants.map((peer) => peer.foreground).sort()).toEqual([
      false,
      true,
    ]);
    const source = a.participants.find(
      (peer) => peer.focus?.target.kind === "file",
    )!;
    expect(source.availability).toBe("available");
    const link = new URL(pageLink(source.focus!)!, app.origin);
    expect(link.pathname).toBe(`/threads/${thread.thread_id}`);
    expect(nativeLink(link.search).path).toBe(path);
    peers[1].report(true);
    await wait(() =>
      expect(peers[0].state.frame?.same_page_participant_ids).toHaveLength(1),
    );
    expect(
      peers[0].state.frame?.participants.find(
        (peer) => peer.participant_id === peers[0].state.frame?.participant_id,
      )?.focus?.target,
    ).toEqual({ kind: "file", path });
  } finally {
    peers.forEach((peer) => peer.socket.close());
  }
}, 15000);
