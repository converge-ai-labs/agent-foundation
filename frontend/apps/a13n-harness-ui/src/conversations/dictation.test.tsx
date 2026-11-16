// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Dictation } from "./dictation";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { recordAudio } from "./dictation-audio";
import { i18n } from "../i18n";
vi.mock("./dictation-audio", () => ({
  recordAudio: vi.fn(),
  encodeAudio: async () => "recorded-audio",
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
it("retries with the original audio and reading context and inserts the response once", async () => {
  await i18n.changeLanguage("en");
  const finish = vi.fn(async () => new Blob(["sample"]));
  vi.mocked(recordAudio).mockResolvedValue(finish);
  const post = vi
    .fn()
    .mockRejectedValueOnce(new Error("Unavailable"))
    .mockResolvedValueOnce({ data: { text: "Run Pyright." } });
  const transport = { client: { POST: post } } as unknown as Transport;
  const insert = vi.fn(),
    onActive = vi.fn(),
    reader = document.createElement("div");
  reader.innerHTML = '<p data-reply-source="reply">Check Pyright now</p>';
  render(
    <TransportContext value={transport}>
      <Dictation
        target={{ title: "Type check", reader: { current: reader }, words: 4 }}
        quote={{ source: "reply", start: 6, text: "Pyright", raw: "Pyright" }}
        readDraft={() => ({ before: "Run ", selected: "Mypy", after: " now" })}
        insert={insert}
        onActive={onActive}
      />
    </TransportContext>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Voice input" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Stop recording" }),
  );
  const retry = await screen.findByRole("button", { name: "Retry" });
  reader.textContent = "Different chat text";
  expect(insert).not.toHaveBeenCalled();
  fireEvent.click(retry);
  await waitFor(() =>
    expect(insert).toHaveBeenCalledExactlyOnceWith("Run Pyright."),
  );
  expect(post.mock.calls[0][1].body.context).toContain(
    "Selected assistant text: Pyright",
  );
  expect(post.mock.calls[1][1].body).toEqual(post.mock.calls[0][1].body);
  expect(recordAudio).toHaveBeenCalledTimes(1);
  expect(finish).toHaveBeenCalledTimes(1);
});

function setup(post: ReturnType<typeof vi.fn>, insert = vi.fn()) {
  vi.mocked(recordAudio).mockResolvedValue(
    vi.fn(async () => new Blob(["sample"])),
  );
  const onActive = vi.fn();
  const view = render(
    <TransportContext
      value={{ client: { POST: post } } as unknown as Transport}
    >
      <Dictation
        target={{ words: 4 }}
        onActive={onActive}
        readDraft={() => ({ before: "Please ", selected: "old", after: "." })}
        insert={insert}
      />
    </TransportContext>,
  );
  return { ...view, insert, onActive };
}
async function record() {
  fireEvent.click(screen.getByRole("button", { name: "Voice input" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Stop recording" }),
  );
}
it("waits for the final text and inserts it once without preview or confirmation", async () => {
  let resolve!: (value: unknown) => void;
  const { insert, onActive } = setup(
    vi.fn(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    ),
  );
  await record();
  await waitFor(() => expect(resolve).toBeTypeOf("function"));
  expect(insert).not.toHaveBeenCalled();
  expect(onActive).toHaveBeenLastCalledWith(true);
  resolve({ data: { text: "new speech", warning: "cleanup_failed" } });
  await waitFor(() =>
    expect(insert).toHaveBeenCalledExactlyOnceWith("new speech"),
  );
  expect(onActive).toHaveBeenLastCalledWith(false);
  expect(screen.queryByText("new speech")).toBeNull();
  expect(screen.getByRole("button", { name: "Voice input" })).toBeTruthy();
  expect(
    screen.getByText("Cleanup unavailable; using the original transcript."),
  ).toBeTruthy();
});
it("cancellation ignores a late server response", async () => {
  let resolve!: (value: unknown) => void;
  const { insert } = setup(
    vi.fn(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    ),
  );
  await record();
  await waitFor(() => expect(resolve).toBeTypeOf("function"));
  fireEvent.click(screen.getByRole("button", { name: "Cancel voice input" }));
  resolve({ data: { text: "late" } });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Voice input" })).toBeTruthy(),
  );
  expect(insert).not.toHaveBeenCalled();
});
it("unmount aborts the request and never inserts", async () => {
  let signal: AbortSignal | undefined;
  let resolve!: (value: unknown) => void;
  const { unmount, insert } = setup(
    vi.fn((_, options) => {
      signal = options.signal;
      return new Promise((done) => {
        resolve = done;
      });
    }),
  );
  await record();
  await waitFor(() => expect(signal).toBeDefined());
  unmount();
  expect(signal!.aborted).toBe(true);
  resolve({ data: { text: "late" } });
  expect(insert).not.toHaveBeenCalled();
});
