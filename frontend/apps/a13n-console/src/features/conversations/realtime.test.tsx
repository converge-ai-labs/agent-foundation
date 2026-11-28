import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, useNavigate, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { RealtimeCallProvider, RealtimeComposer } from "./realtime";

const post = vi.hoisted(() => vi.fn());
const get = vi.hoisted(() => vi.fn());
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: {}, workspace: () => ({ POST: post, GET: get }) }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "workspace" },
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const mediaDescriptor = Object.getOwnPropertyDescriptor(
  navigator,
  "mediaDevices",
);
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  if (mediaDescriptor)
    Object.defineProperty(navigator, "mediaDevices", mediaDescriptor);
  else Reflect.deleteProperty(navigator, "mediaDevices");
  post.mockReset();
  get.mockReset();
});

const response = (data: object) => ({ response: new Response(), data });
const receipt = (id: number) => ({
  session_id: "session",
  thread_id: "thread",
  run_id: `run-${id}`,
});
const iceServers = [
  {
    urls: ["turn:relay.example.org:3478"],
    username: "user",
    credential: "test",
  },
];

function harness({
  rtc = false,
  images = false,
  iceFailure = false,
  singleDevice = false,
} = {}) {
  const sockets: Socket[] = [];
  const contexts: Capture[] = [];
  const sources: {
    connect: ReturnType<typeof vi.fn>;
    disconnect: ReturnType<typeof vi.fn>;
  }[] = [];
  const tracks: ReturnType<typeof track>[] = [];
  const rtcConfig = vi.fn();
  const replaceTrack = vi.fn(async (_track: unknown) => {});
  const tickets: {
    transport: string;
    thread_id?: string;
    draft?: string;
    draft_asset_ids?: string[];
  }[] = [];
  let level = 0;
  post.mockImplementation(async (path: string, { body }) => {
    if (path === "/api/v1/uploads")
      return response({ upload_id: "upload", filename: body.file.name });
    if (path === "/api/v1/assets")
      return response({ id: "ast_photo", name: body.name });
    expect(path).toBe("/api/v1/realtime/sessions");
    tickets.push(body);
    return response({
      ...receipt(tickets.length),
      websocket_path: "/realtime",
      subprotocol: "test",
      ticket: "ticket",
      ice_servers: iceServers,
    });
  });
  get.mockResolvedValue(response({ sealed_at: "saved" }));
  class Socket {
    static OPEN = 1;
    static CLOSING = 2;
    readyState = Socket.OPEN;
    bufferedAmount = 0;
    send = vi.fn();
    close = vi.fn(() => {
      this.readyState = Socket.CLOSING;
    });
    onopen?: () => void;
    onmessage?: (event: { data: string | ArrayBuffer }) => void;
    onclose?: (event: { code: number }) => void;
    constructor() {
      sockets.push(this);
    }
    receive(frame: object) {
      act(() => this.onmessage?.({ data: JSON.stringify(frame) }));
    }
    accepted(id = 1) {
      this.receive({ type: "accepted", ...receipt(id) });
    }
    ready() {
      this.receive({
        type: "ready",
        accepts_text: true,
        audio_input_hz: 16000,
        audio_output_hz: 24000,
      });
    }
    end(code = 1006) {
      this.close();
      return act(async () => this.onclose?.({ code }));
    }
  }
  class Capture {
    port = {
      onmessage: undefined as ((event: { data: unknown }) => void) | undefined,
      postMessage: vi.fn(),
    };
    connect = vi.fn();
    disconnect = vi.fn();
    constructor(
      _context: unknown,
      _name: string,
      public options: AudioWorkletNodeOptions,
    ) {
      contexts.push(this);
    }
  }
  function track(id: string) {
    return {
      enabled: true,
      stop: vi.fn(),
      getSettings: () => ({ deviceId: id }),
    };
  }
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("AudioWorkletNode", Capture);
  vi.stubGlobal(
    "Audio",
    class {
      pause = vi.fn();
    },
  );
  vi.stubGlobal(
    "AudioContext",
    class {
      audioWorklet = { addModule: async () => {} };
      resume = vi.fn(async () => {});
      close = vi.fn(async () => {});
      createAnalyser = () => ({
        fftSize: 0,
        getFloatTimeDomainData: (values: Float32Array) => values.fill(level),
        connect: vi.fn(),
      });
      createMediaStreamSource = () => {
        const source = { connect: vi.fn(), disconnect: vi.fn() };
        sources.push(source);
        return source;
      };
    },
  );
  vi.stubGlobal(
    "RTCPeerConnection",
    rtc
      ? class {
          iceGatheringState = "complete";
          connectionState = "connected";
          localDescription = { sdp: "local SDP" };
          constructor(config: RTCConfiguration) {
            rtcConfig(config);
          }
          createOffer = async () => {
            if (iceFailure) throw new Error("ICE unavailable");
            return this.localDescription;
          };
          setLocalDescription = async () => {};
          close = vi.fn();
          addTrack = () => ({ replaceTrack });
        }
      : undefined,
  );
  const devices = (singleDevice ? ["default"] : ["default", "usb"]).map(
    (deviceId) => ({
      kind: "audioinput",
      deviceId,
      label: `${deviceId} microphone`,
    }),
  );
  const getUserMedia = vi.fn(async (constraints: MediaStreamConstraints) => {
    const audio = constraints.audio;
    const requested = typeof audio === "object" && audio.deviceId;
    const id =
      (requested &&
        typeof requested === "object" &&
        !Array.isArray(requested) &&
        requested.exact) ||
      "default";
    const selected = track(String(id));
    tracks.push(selected);
    return { getTracks: () => [selected], getAudioTracks: () => [selected] };
  });
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia, enumerateDevices: async () => devices },
  });
  function Dial() {
    const navigate = useNavigate();
    const displayedRunId = useLocation().pathname.split("/runs/")[1];
    return (
      <RealtimeComposer
        agentId="agent"
        displayedRunId={displayedRunId}
        availability={{
          realtime: true,
          can_start: !displayedRunId,
          accepts_text: true,
          accepts_images: images,
          supported_image_types: images ? ["image/png"] : [],
          max_images_per_input: images ? 1 : 0,
          transports: rtc ? ["webrtc", "websocket"] : ["websocket"],
        }}
        onAccepted={({ session_id, thread_id, run_id }) =>
          navigate(
            `/workspace/design/sessions/${session_id}/threads/${thread_id}/runs/${run_id}`,
          )
        }
      />
    );
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <RealtimeCallProvider>
          <Dial />
        </RealtimeCallProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  async function dial() {
    fireEvent.click(screen.getByRole("button", { name: "Start voice chat" }));
    await waitFor(() => expect(sockets[0]).toBeDefined());
    act(() => sockets[0]!.onopen?.());
    return sockets[0]!;
  }
  return {
    dial,
    tickets,
    sockets,
    tracks,
    sources,
    contexts,
    rtcConfig,
    replaceTrack,
    getUserMedia,
    output: (value: number) => {
      level = value;
    },
  };
}

it("shows Alpha and sends an opening image-only draft, then a typed image without disconnecting on rejection", async () => {
  const { tickets, sockets } = harness({ images: true });
  expect(screen.getByText("Alpha")).toBeTruthy();
  async function attach(name: string) {
    fireEvent.click(screen.getByRole("button", { name: "Attach image" }));
    const dialog = await screen.findByRole("dialog");
    const picker =
      dialog.querySelector<HTMLInputElement>('input[type="file"]')!;
    expect(picker.accept).toBe("image/png");
    expect(screen.getByText(/Drop an image here/)).toBeTruthy();
    fireEvent.change(picker, {
      target: { files: [new File(["test"], name, { type: "image/png" })] },
    });
    await screen.findByText(name);
    expect(
      dialog.querySelector<HTMLInputElement>('input[type="file"]')!.disabled,
    ).toBe(true);
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  }
  await attach("opening.png");
  fireEvent.click(screen.getByRole("button", { name: "Start voice chat" }));
  await waitFor(() => expect(sockets[0]).toBeDefined());
  expect(tickets[0]).toMatchObject({
    draft: "",
    draft_asset_ids: ["ast_photo"],
  });
  sockets[0]!.accepted();
  sockets[0]!.ready();
  await attach("during.png");
  fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
    target: { value: "Read this" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() =>
    expect(sockets[0]!.send).toHaveBeenCalledWith(
      JSON.stringify({
        type: "input",
        text: "Read this",
        asset_ids: ["ast_photo"],
        respond: true,
      }),
    ),
  );
  sockets[0]!.receive({
    type: "error",
    code: "invalid_argument",
    message: "Choose an active image",
    recoverable: true,
  });
  expect(screen.getByText("Choose an active image")).toBeTruthy();
  expect(sockets[0]!.close).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "End call" })).toBeTruthy();
});

it.each([false, true])(
  "mutes capture and changes microphones without replacing the Run (WebRTC: %s)",
  async (rtc) => {
    const {
      dial,
      tickets,
      tracks,
      replaceTrack,
      sources,
      contexts,
      rtcConfig,
      getUserMedia,
      output,
    } = harness({ rtc });
    const socket = await dial();
    if (rtc) expect(rtcConfig).toHaveBeenCalledWith({ iceServers });
    socket.accepted();
    socket.ready();
    expect(screen.getByText(rtc ? /· WebRTC/ : /· PCM/)).toBeTruthy();
    expect(contexts[0]!.options.processorOptions).toEqual({
      inputHz: 16000,
      pcm: !rtc,
    });
    const capture = contexts[0]!.port;
    const input = screen.getByRole("meter", { name: "Microphone level" });
    const speaker = screen.getByRole("meter", { name: "Speaker level" });
    output(0.1);
    capture.onmessage?.({ data: { level: 0.25 } });
    await waitFor(() =>
      expect(Number(input.getAttribute("aria-valuenow"))).toBeCloseTo(0.75),
    );
    if (!rtc)
      expect(Number(speaker.getAttribute("aria-valuenow"))).toBeCloseTo(0.3);
    socket.receive({ type: "limit_warning", remaining_seconds: 25 });
    expect(screen.getByText("Ending soon")).toBeTruthy();
    expect(screen.getByLabelText("Time remaining").textContent).toBe("00:25");
    fireEvent.click(screen.getByRole("button", { name: "Mute microphone" }));
    expect(tracks[0]!.enabled).toBe(false);
    expect(capture.postMessage).toHaveBeenLastCalledWith({ muted: true });
    socket.send.mockClear();
    capture.onmessage?.({ data: new ArrayBuffer(32) });
    capture.onmessage?.({ data: "speech" });
    expect(socket.send).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Microphone device" }));
    expect(
      (
        await screen.findByRole("menuitemradio", { name: "default microphone" })
      ).getAttribute("aria-checked"),
    ).toBe("true");
    fireEvent.click(
      screen.getByRole("menuitemradio", { name: "usb microphone" }),
    );
    await waitFor(() => expect(tracks[0]!.stop).toHaveBeenCalledOnce());
    expect(getUserMedia).toHaveBeenLastCalledWith({
      audio: expect.objectContaining({ deviceId: { exact: "usb" } }),
    });
    expect(tracks[1]!.enabled).toBe(false);
    expect(sources[0]!.disconnect).toHaveBeenCalledOnce();
    expect(sources[1]!.connect).toHaveBeenCalledWith(contexts[0]);
    if (rtc) expect(replaceTrack).toHaveBeenCalledWith(tracks[1]);
    else expect(replaceTrack).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Unmute microphone" }));
    expect(tracks[1]!.enabled).toBe(true);
    expect(capture.postMessage).toHaveBeenLastCalledWith({ muted: false });
    expect(tickets).toHaveLength(1);
    expect(socket.close).not.toHaveBeenCalled();
  },
);

it("retries one locally failed WebRTC startup as PCM", async () => {
  const { dial, tickets } = harness({ rtc: true, iceFailure: true });
  await dial();
  expect(tickets.map(({ transport }) => transport)).toEqual([
    "webrtc",
    "websocket",
  ]);
  expect(
    screen.getByText("WebRTC could not connect. Switching to PCM audio."),
  ).toBeTruthy();
});

it("retries a sealed pre-ready WebRTC transport failure on the same Thread using PCM", async () => {
  const { dial, tickets, sockets } = harness({ rtc: true });
  const socket = await dial();
  socket.accepted();
  socket.receive({
    type: "error",
    code: "realtime_transport_failed",
    message: "ICE failed",
  });
  socket.receive({
    type: "sealed",
    run_id: "run-1",
    reason: "realtime_transport_failed",
  });
  await socket.end();
  await waitFor(() => expect(sockets).toHaveLength(2));
  expect(tickets[1]).toMatchObject({
    thread_id: "thread",
    transport: "websocket",
  });
});

it("does not fall back when the sealed Run reports an endpoint refusal", async () => {
  const { dial, tickets } = harness({ rtc: true });
  const socket = await dial();
  socket.accepted();
  get.mockResolvedValue(
    response({
      sealed_at: "saved",
      failure: { code: "provider_endpoint_denied", message: "Endpoint denied" },
    }),
  );
  socket.receive({
    type: "error",
    code: "realtime_transport_failed",
    message: "ICE failed",
  });
  await socket.end();
  expect(screen.getByText("Endpoint denied")).toBeTruthy();
  expect(tickets).toHaveLength(1);
  expect(
    screen.queryByRole("button", { name: "Resume voice call" }),
  ).toBeNull();
});

it("waits for seal, then automatically reconnects once after Worker drain", async () => {
  const { dial, tickets, sockets } = harness();
  const socket = await dial();
  socket.accepted();
  socket.ready();
  get.mockResolvedValueOnce(response({ sealed_at: null }));
  vi.useFakeTimers();
  socket.receive({ type: "draining", code: "worker_draining" });
  await socket.end(1012);
  expect(tickets).toHaveLength(1);
  await act(async () => vi.advanceTimersByTimeAsync(500));
  expect(sockets).toHaveLength(2);
  expect(tickets[1]).toMatchObject({
    thread_id: "thread",
    transport: "websocket",
  });
  sockets[1]!.accepted(2);
  sockets[1]!.ready();
  sockets[1]!.receive({ type: "draining", code: "worker_draining" });
  sockets[1]!.receive({
    type: "sealed",
    run_id: "run-2",
    reason: "worker_draining",
  });
  await sockets[1]!.end(1012);
  expect(tickets).toHaveLength(2);
  expect(
    screen.getByRole("button", { name: "Resume voice call" }),
  ).toBeTruthy();
});

it("offers manual continuation after an unexpected accepted socket drop", async () => {
  const { dial, tickets } = harness({ singleDevice: true });
  const socket = await dial();
  socket.accepted();
  socket.ready();
  expect(
    screen.queryByRole("button", { name: "Microphone device" }),
  ).toBeNull();
  expect(screen.queryByText("Alpha")).toBeNull();
  await socket.end();
  expect(tickets).toHaveLength(1);
  expect(
    screen.getByText(
      "Voice connection dropped. You can resume in this conversation.",
    ),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Resume voice call" }));
  await waitFor(() =>
    expect(tickets[1]).toMatchObject({ thread_id: "thread" }),
  );
});

it.each([
  ["duration_limit", "Voice call reached its time limit."],
  ["idle_timeout", "Voice call ended after being idle."],
  ["usage_limit_exceeded", "Voice call reached its usage limit."],
  ["authority_revoked", "You no longer have permission to continue this call."],
])("ends without reconnect on %s", async (reason, message) => {
  const { dial, tickets } = harness();
  const socket = await dial();
  socket.accepted();
  socket.ready();
  socket.receive({ type: "ending", reason });
  socket.receive({ type: "sealed", run_id: "run-1", reason });
  await socket.end(1000);
  expect(screen.getByText(message)).toBeTruthy();
  expect(tickets).toHaveLength(1);
  expect(
    screen.queryByRole("button", { name: "Resume voice call" }),
  ).toBeNull();
});
