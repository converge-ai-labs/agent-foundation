// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { VoiceSession } from "./voice-session";
import { createTransport } from "./transport/client";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
class Socket {
  static instances: Socket[] = [];
  onopen?: () => void;
  onmessage?: (event: { data: string }) => void;
  onerror?: () => void;
  onclose?: (event: { reason: string }) => void;
  send = vi.fn();
  close = vi.fn();
  constructor(readonly url: URL) {
    Socket.instances.push(this);
  }
  frame(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}
class Peer extends EventTarget {
  static instances: Peer[] = [];
  connectionState = "new";
  iceGatheringState = "complete";
  localDescription = { sdp: "test-offer" };
  onconnectionstatechange?: () => void;
  ontrack?: (event: { streams: unknown[] }) => void;
  addTrack = vi.fn();
  createDataChannel = vi.fn();
  createOffer = vi.fn(async () => ({ type: "offer", sdp: "test-offer" }));
  setLocalDescription = vi.fn(async () => {});
  setRemoteDescription = vi.fn(async () => {});
  close = vi.fn();
  constructor() {
    super();
    Peer.instances.push(this);
  }
  connected() {
    this.connectionState = "connected";
    this.onconnectionstatechange?.();
  }
}
class Audio {
  static instances: Audio[] = [];
  srcObject: unknown = null;
  play = vi.fn(async () => {});
  pause = vi.fn();
  constructor() {
    Audio.instances.push(this);
  }
}
const track = { stop: vi.fn(), onended: null as (() => void) | null };
const stream = { getTracks: () => [track] };
const media = vi.fn(async () => stream);
const fetchMock = vi.fn(
  async (_request: Request) =>
    new Response(JSON.stringify({ status: "starting" }), { status: 202 }),
);
let session: VoiceSession;
beforeEach(() => {
  vi.useFakeTimers();
  Socket.instances = [];
  Peer.instances = [];
  Audio.instances = [];
  media.mockReset().mockResolvedValue(stream);
  fetchMock
    .mockReset()
    .mockImplementation(
      async () =>
        new Response(JSON.stringify({ status: "starting" }), { status: 202 }),
    );
  vi.stubGlobal("window", { location: { origin: "http://localhost" } });
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: media } });
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("RTCPeerConnection", Peer);
  vi.stubGlobal("Audio", Audio);
  vi.stubGlobal("fetch", fetchMock);
  session = new VoiceSession(createTransport("test-key", vi.fn()));
});
afterEach(() => {
  session.stop();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
async function claim() {
  const pending = session.start();
  const socket = Socket.instances.at(-1)!;
  socket.onopen?.();
  socket.frame({ status: "idle" });
  await Promise.resolve();
  return { pending, socket };
}
it("requires an explicit start, claims controls before microphone and creates exactly one call", async () => {
  expect(media).not.toHaveBeenCalled();
  expect(Socket.instances).toHaveLength(0);
  const pending = session.start();
  await session.start();
  expect(Socket.instances).toHaveLength(1);
  expect(media).not.toHaveBeenCalled();
  const socket = Socket.instances[0]!;
  socket.onopen?.();
  expect(socket.send).toHaveBeenNthCalledWith(
    1,
    JSON.stringify({ api_key: "test-key" }),
  );
  socket.frame({ status: "idle" });
  await pending;
  expect(media).toHaveBeenCalledTimes(1);
  expect(Peer.instances).toHaveLength(1);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const request = fetchMock.mock.calls[0]![0] as unknown as Request;
  expect(request.url).toBe("http://localhost/api/voice/start");
  const body = await request.json();
  expect(body.offer_sdp).toBe("test-offer");
  expect(request.headers.get("authorization")).toBe("Bearer test-key");
  socket.frame({ start_id: "stale-call", answer_sdp: "stale" });
  expect(Peer.instances[0]!.setRemoteDescription).not.toHaveBeenCalled();
  socket.frame({
    start_id: body.start_id,
    status: "connecting",
    answer_sdp: "answer",
  });
  socket.frame({
    start_id: body.start_id,
    status: "connecting",
    answer_sdp: "answer",
  });
  expect(Peer.instances[0]!.setRemoteDescription).toHaveBeenCalledTimes(1);
  Peer.instances[0]!.connected();
  expect(socket.send).toHaveBeenLastCalledWith(
    JSON.stringify({ kind: "media_connected", start_id: body.start_id }),
  );
  socket.frame({ start_id: body.start_id, status: "active" });
  expect(session.getSnapshot().status).toBe("active");
  expect(vi.getTimerCount()).toBe(0);
  session.stop();
  expect(track.stop).toHaveBeenCalledTimes(1);
  expect(Peer.instances[0]!.close).toHaveBeenCalledTimes(1);
  expect(socket.close).toHaveBeenCalledTimes(1);
  expect(Audio.instances[0]!.pause).toHaveBeenCalledTimes(1);
  expect(Audio.instances[0]!.srcObject).toBeNull();
});
it("stops late microphone permission without opening media or posting an offer", async () => {
  const permission = deferred<typeof stream>();
  media.mockReturnValueOnce(permission.promise);
  const { pending } = await claim();
  expect(media).toHaveBeenCalledTimes(1);
  session.stop();
  permission.resolve(stream);
  await pending;
  expect(track.stop).toHaveBeenCalledTimes(1);
  expect(Peer.instances).toHaveLength(0);
  expect(fetchMock).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});
it("cancels an in-flight bootstrap and ignores late media events without replay", async () => {
  const response = deferred<Response>();
  fetchMock.mockReturnValueOnce(response.promise);
  const { pending } = await claim();
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const request = fetchMock.mock.calls[0]![0] as unknown as Request;
  session.stop();
  expect(request.signal.aborted).toBe(true);
  Peer.instances[0]!.ontrack?.({ streams: [stream] });
  expect(Audio.instances[0]!.play).not.toHaveBeenCalled();
  response.resolve(
    new Response(JSON.stringify({ status: "starting" }), { status: 202 }),
  );
  await pending;
  await vi.advanceTimersByTimeAsync(65_000);
  expect(session.getSnapshot().status).toBe("idle");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});
it("reports control failure and requires another user start rather than reconnecting", async () => {
  const { pending, socket } = await claim();
  await pending;
  socket.onclose?.({ reason: "Another tab owns Live" });
  expect(session.getSnapshot()).toMatchObject({
    status: "failed",
    error: "Another tab owns Live",
  });
  await vi.advanceTimersByTimeAsync(65_000);
  expect(Socket.instances).toHaveLength(1);
  expect(track.stop).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});
it("releases controls on denied microphone permission", async () => {
  media.mockRejectedValueOnce(new Error("Permission denied"));
  const { pending, socket } = await claim();
  await pending;
  expect(session.getSnapshot()).toMatchObject({
    status: "failed",
    error: "Permission denied",
  });
  expect(socket.close).toHaveBeenCalledTimes(1);
  expect(fetchMock).not.toHaveBeenCalled();
});
it("releases a startup timeout even after the start receipt has returned", async () => {
  const { pending } = await claim();
  await pending;
  expect(vi.getTimerCount()).toBe(1);
  session.stop();
  expect(vi.getTimerCount()).toBe(0);
});
