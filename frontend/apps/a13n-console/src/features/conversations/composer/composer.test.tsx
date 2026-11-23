import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { RealtimeComposer } from "../realtime";
import { Composer } from "./composer";

vi.mock("../../../auth/context", () => ({
  useClient: () => ({ http: {}, workspace: () => ({}) }),
}));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "workspace" },
    can: () => false,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

function mount(submit: (input: unknown, key: string) => Promise<void | false>) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      }
    >
      <Composer submit={submit} />
    </QueryClientProvider>,
  );
  return screen.getByRole("textbox", { name: "Message" });
}
it("preserves a failed draft and its command key until the retry succeeds", async () => {
  const submit = vi
    .fn()
    .mockRejectedValueOnce(new Error("Try again"))
    .mockResolvedValueOnce(undefined);
  const input = mount(submit);
  fireEvent.change(input, { target: { value: "Keep this draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByRole("alert");
  expect((input as HTMLTextAreaElement).value).toBe("Keep this draft");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe(""));
  expect(submit).toHaveBeenCalledTimes(2);
  expect(submit.mock.calls[0]).toEqual(submit.mock.calls[1]);
});
it.each(["metaKey", "ctrlKey"])(
  "sends with %s + Enter while preserving newlines and IME composition",
  async (modifier) => {
    const submit = vi.fn().mockResolvedValue(undefined);
    const input = mount(submit);
    const user = userEvent.setup();
    await user.type(input, "A message{Enter}Second line");
    expect((input as HTMLTextAreaElement).value).toBe("A message\nSecond line");
    fireEvent.keyDown(input, {
      key: "Enter",
      [modifier]: true,
      shiftKey: true,
    });
    fireEvent.keyDown(input, {
      key: "Enter",
      [modifier]: true,
      isComposing: true,
    });
    expect(submit).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter", [modifier]: true });
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]![0].content).toEqual([
      { type: "text", text: "A message\nSecond line" },
    ]);
  },
);
it("retains message and attachment drafts when closing the attachment dialog", async () => {
  const user = userEvent.setup(),
    submit = vi.fn().mockResolvedValue(undefined);
  const input = mount(submit);
  await user.type(input, "Read this file");
  await user.click(screen.getByRole("button", { name: "Attach file or URL" }));
  await user.type(
    screen.getByRole("textbox", { name: "File URL" }),
    "https://example.com/report.pdf",
  );
  await user.click(screen.getByRole("button", { name: "Attach URL" }));
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect((input as HTMLTextAreaElement).value).toBe("Read this file");
  await user.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  expect(submit.mock.calls[0]![0].content).toContainEqual({
    type: "url",
    url: "https://example.com/report.pdf",
  });
});

it("keeps the waveform action with and without a realtime draft", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RealtimeComposer
        agentId="agent"
        availability={{ realtime: true, can_start: true, accepts_text: true }}
        onFinished={vi.fn()}
      />
    </QueryClientProvider>,
  );
  const input = screen.getByRole("textbox", { name: "Message" });
  expect(input).toBeInstanceOf(HTMLTextAreaElement);
  const voice = screen.getByRole("button", { name: "Start voice chat" });
  expect(voice.closest("form")).toBe(input.closest("form"));
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull();

  fireEvent.change(input, { target: { value: "Typed input" } });
  const withDraft = screen.getByRole("button", { name: "Start voice chat" });
  expect(withDraft.className).toBe(voice.className);
  expect(withDraft.querySelector("svg")?.outerHTML).toBe(
    voice.querySelector("svg")?.outerHTML,
  );
  expect((withDraft as HTMLButtonElement).disabled).toBe(false);
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
});

it("keeps an unsent draft when opening the microphone is cancelled", async () => {
  let finishPermission!: (stream: MediaStream) => void;
  const permission = new Promise<MediaStream>((resolve) => {
    finishPermission = resolve;
  });
  const getUserMedia = vi.fn(() => permission);
  const track = { stop: vi.fn() };
  const close = vi.fn(async () => {});
  const originalMedia = Object.getOwnPropertyDescriptor(
    navigator,
    "mediaDevices",
  );
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
  vi.stubGlobal(
    "AudioContext",
    class {
      resume = async () => {};
      close = close;
    },
  );
  const connect = vi.fn();
  vi.stubGlobal(
    "WebSocket",
    class {
      constructor() {
        connect();
      }
    },
  );
  try {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RealtimeComposer
          agentId="agent"
          initial={{ content: [{ type: "text", text: "Keep this draft" }] }}
          availability={{ realtime: true, can_start: true, accepts_text: true }}
          onFinished={vi.fn()}
        />
      </QueryClientProvider>,
    );
    const input = screen.getByRole<HTMLTextAreaElement>("textbox", {
      name: "Message",
    });
    const start = screen.getByRole<HTMLButtonElement>("button", {
      name: "Start voice chat",
    });
    fireEvent.click(start);
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "End realtime chat" }));
    finishPermission({ getTracks: () => [track] } as unknown as MediaStream);
    await waitFor(() => {
      expect(start.disabled).toBe(false);
      expect(track.stop).toHaveBeenCalledOnce();
    });
    expect(close).toHaveBeenCalledOnce();
    expect(connect).not.toHaveBeenCalled();
    expect(input.value).toBe("Keep this draft");
    expect(screen.queryByRole("alert")).toBeNull();
  } finally {
    vi.unstubAllGlobals();
    if (originalMedia)
      Object.defineProperty(navigator, "mediaDevices", originalMedia);
    else Reflect.deleteProperty(navigator, "mediaDevices");
  }
});

it("uses Enter to open a voice session or submit its draft while preserving IME and Shift+Enter", async () => {
  const submit = vi.fn(async (_payload: unknown) => {});
  const open = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Composer
        submit={submit}
        submitOnEnter
        emptyAction={{
          label: "Start voice chat",
          icon: <span />,
          onClick: open,
        }}
      />
    </QueryClientProvider>,
  );
  const input = screen.getByRole("textbox", { name: "Message" });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(open).toHaveBeenCalledOnce();

  fireEvent.change(input, { target: { value: "Typed input" } });
  fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  expect(submit).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(submit).toHaveBeenCalledOnce());
  expect(submit.mock.calls[0]![0]).toEqual({
    content: [{ type: "text", text: "Typed input" }],
  });
});
