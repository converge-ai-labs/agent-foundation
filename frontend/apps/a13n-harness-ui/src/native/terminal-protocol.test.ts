import { afterAll, beforeAll, expect, it, vi } from "vitest";
import { startNativeApp } from "../../tests/app-fixture";
import { createTransport, result, type Transport } from "../transport/client";
import { TerminalConnection } from "./terminal-connection";
import { FileBuffer, joinPath } from "./buffer";

let app: Awaited<ReturnType<typeof startNativeApp>>;
let transport: Transport;
const connections: TerminalConnection[] = [];
beforeAll(async () => {
  app = await startNativeApp();
  vi.stubGlobal("window", { location: { origin: app.origin } });
  transport = createTransport("test-only-key", () => {});
}, 40000);
afterAll(async () => {
  connections.forEach((session) => session.dispose());
  transport?.close();
  await app?.close();
  vi.unstubAllGlobals();
}, 20000);
function attach(id: string, cursor = 0) {
  let output = "";
  const session = new TerminalConnection(
    id,
    {
      write: (text, done) => {
        output += text;
        done();
      },
      reset: () => {
        output += "[GAP]";
      },
    },
    () => {},
    () => {},
  );
  session.cursor = cursor;
  connections.push(session);
  session.connect(app.origin, "test-only-key");
  return { session, output: () => output };
}
const wait = (assertion: () => void) =>
  vi.waitFor(assertion, { timeout: 10000, interval: 30 });

it("two real native PTY viewers transfer epoch-fenced control, resize, edit and stage without affecting dirty file buffers", async () => {
  const status = await result(transport.client.GET("/api/status"));
  if (process.platform === "win32") {
    expect(status.features?.host_terminal).toBe(false);
    await expect(
      result(
        transport.client.POST("/api/host/terminals", {
          body: { cwd: app.native_root },
        }),
      ),
    ).rejects.toThrow();
    return;
  }
  expect(status.features?.host_terminal).toBe(true);
  const repository = joinPath(app.native_root, "repository");
  const tracked = joinPath(repository, "tracked.txt");
  const read = () =>
    result(
      transport.client.GET("/api/host/files/text", {
        params: { query: { path: tracked } },
      }),
    );
  const buffer = new FileBuffer(await read());
  buffer.value = "private browser draft";
  const created = await result(
    transport.client.POST("/api/host/terminals", { body: { cwd: repository } }),
  );
  const a = attach(created.terminal_id),
    b = attach(created.terminal_id);
  await wait(() => {
    expect(a.session.state.connection).toBe("Live");
    expect(b.session.state.frame?.terminal.participants).toHaveLength(2);
  });
  expect(a.session.controls).toBe(false);
  expect(b.session.input("not allowed\r")).toBe(false);
  a.session.control();
  await wait(() => expect(a.session.controls).toBe(true));
  a.session.resize(31, 101);
  await wait(() => expect(b.session.state.frame?.terminal.columns).toBe(101));
  a.session.input("stty -echo\r");
  await new Promise((resolve) => setTimeout(resolve, 100));
  a.session.input("printf '\\nPTY_BEFORE_TAKEOVER\\n'\r");
  await wait(() => expect(b.output()).toContain("PTY_BEFORE_TAKEOVER"));
  b.session.control();
  await wait(() => {
    expect(b.session.controls).toBe(true);
    expect(a.session.controls).toBe(false);
  });
  expect(a.session.input("stale\r")).toBe(false);
  expect(a.session.resize(9, 9)).toBe(false);
  b.session.input(
    "printf 'from terminal\\n' > tracked.txt; git add -- tracked.txt; printf '\\nGIT_DONE\\n'\r",
  );
  await wait(() => expect(a.output()).toContain("GIT_DONE"));
  await vi.waitFor(
    async () => expect((await read()).text).toBe("from terminal\n"),
    { timeout: 10000 },
  );
  buffer.observe(await read());
  expect(buffer.value).toBe("private browser draft");
  expect(buffer.conflict).toBe(true);
  const diff = await result(
    transport.client.GET("/api/host/git/diff", {
      params: {
        query: {
          repository_path: repository,
          path: "tracked.txt",
          comparison: "staged",
        },
      },
    }),
  );
  expect(diff.text).toContain("+from terminal");
  const rootThread = await result(
    transport.client.POST("/api/threads", { body: {} }),
  );
  expect(rootThread.thread_id).toBeTruthy();
  expect(
    (await result(transport.client.GET("/api/host/terminals"))).map(
      (item) => item.terminal_id,
    ),
  ).toContain(created.terminal_id);
  b.session.detach();
  await wait(() =>
    expect(a.session.state.frame?.terminal.controller).toBeNull(),
  );
  await wait(() => expect(b.session.draining).toBe(false));
  const previousOutput = b.output();
  b.session.connect(app.origin, "test-only-key");
  await wait(() => expect(b.session.state.connection).toBe("Live"));
  expect(b.session.controls).toBe(false);
  expect(b.output()).toBe(previousOutput);
  b.session.control();
  await wait(() => expect(b.session.controls).toBe(true));
  b.session.input("exit 7\r");
  await wait(() => expect(a.session.state.frame?.terminal.exit_code).toBe(7));
  await transport.client.DELETE("/api/host/terminals/{terminal_id}", {
    params: { path: { terminal_id: created.terminal_id } },
  });
  expect(
    (await result(transport.client.GET("/api/host/terminals"))).some(
      (item) => item.terminal_id === created.terminal_id,
    ),
  ).toBe(false);
}, 30000);

it.skipIf(process.platform === "win32")(
  "reattachment beyond retained output discloses a gap; a removed identity is never recreated",
  async () => {
    const created = await result(
      transport.client.POST("/api/host/terminals", {
        body: { cwd: app.native_root },
      }),
    );
    const a = attach(created.terminal_id);
    await wait(() => expect(a.session.state.connection).toBe("Live"));
    a.session.control();
    await wait(() => expect(a.session.controls).toBe(true));
    a.session.input(
      "head -c 1100000 /dev/zero | tr '\\000' x; printf '\\nFLOOD_DONE\\n'\r",
    );
    await wait(() =>
      expect(a.session.state.frame?.terminal.output_end).toBeGreaterThan(
        1048576,
      ),
    );
    a.session.detach();
    const b = attach(created.terminal_id);
    await wait(() => expect(b.output()).toContain("[GAP]"));
    expect(b.session.state.message).toContain("Output gap");
    await transport.client.DELETE("/api/host/terminals/{terminal_id}", {
      params: { path: { terminal_id: created.terminal_id } },
    });
    const gone = attach(created.terminal_id);
    await wait(() => expect(gone.session.state.connection).toBe("Detached"));
    expect(await result(transport.client.GET("/api/host/terminals"))).toEqual(
      [],
    );
  },
  25000,
);

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
