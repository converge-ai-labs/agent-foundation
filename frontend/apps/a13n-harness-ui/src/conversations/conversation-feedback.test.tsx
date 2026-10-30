// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { ApiError, NetworkError, type Transport } from "../transport/client";
import { TransportContext } from "../transport/context";
import { Conversation } from "./conversation";
import { ComposerDrafts } from "./composer";
import { ThreadDraft } from "./draft";
import { previewInput } from "./local-input";

vi.mock("./live-threads", async () => {
  const { FocusDisplay } = await import("./stream");
  const snapshot = {
    display: new FocusDisplay(),
    connection: "Loading current output",
    reconnections: 0,
    revision: 0,
  };
  return { useLiveThread: () => snapshot };
});

it("retains an accepted input when the following detail read fails, without resending it", async () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  const GET = vi.fn().mockRejectedValue(new NetworkError(null));
  const POST = vi.fn();
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const draft = new ThreadDraft();
  vi.spyOn(draft, "connect").mockReturnValue({ presence() {}, close() {} });
  draft.localInputs = [
    {
      id: "accepted",
      action: "send",
      state: "accepted",
      parts: previewInput("accepted", ["Your first message"]),
    },
  ];
  const view = render(
    <QueryClientProvider client={query}>
      <TransportContext
        value={{ client: { GET, POST } } as unknown as Transport}
      >
        <ComposerDrafts value={new Map([["created", draft]])}>
          <MemoryRouter>
            <Conversation
              threadId="created"
              profile={{ display_name: "Tester", color: "#666666" }}
              unauthorized={() => {}}
            />
          </MemoryRouter>
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>,
  );
  try {
    await screen.findByText(/Unable to reach the server/);
    expect(
      screen.getAllByRole("alert").map((node) => node.textContent),
    ).toEqual([expect.stringContaining("Unable to reach the server")]);
    expect(screen.getByText("Your first message")).toBeTruthy();
    expect(
      screen.getByRole("region", { name: "Message composer" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("region", { name: "Chat unavailable" }),
    ).toBeNull();
    expect(draft.localInputs[0].state).toBe("accepted");
    expect(POST).not.toHaveBeenCalled();
  } finally {
    view.unmount();
    query.clear();
    draft.doc.destroy();
  }
});

beforeEach(() => {
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => new DOMRect(),
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("owns a missing chat once despite dependent failures, keeps unsent input, and retries only the read", async () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  const error = new ApiError("Thread does not exist.", 400, "thread_missing");
  const GET = vi.fn().mockRejectedValue(error);
  const POST = vi.fn();
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const draft = new ThreadDraft();
  vi.spyOn(draft, "connect").mockReturnValue({ presence() {}, close() {} });
  draft.doc.getText("text").insert(0, "Keep this unsent input");
  draft.error = error.message;
  const view = render(
    <QueryClientProvider client={query}>
      <TransportContext
        value={{ client: { GET, POST } } as unknown as Transport}
      >
        <ComposerDrafts value={new Map([["missing", draft]])}>
          <MemoryRouter>
            <Conversation
              threadId="missing"
              profile={{ display_name: "Tester", color: "#666666" }}
              unauthorized={() => {}}
            />
          </MemoryRouter>
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>,
  );
  try {
    await screen.findByRole("heading", { name: "Chat not found" });
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.queryByText("Thread does not exist.")).toBeNull();
    expect(
      screen.queryByText(
        /Loading current output|Loading saved history|Refresh agent/,
      ),
    ).toBeNull();
    expect(
      screen.queryByRole("region", { name: "Message composer" }),
    ).toBeNull();
    expect(
      screen.getByText("Your unsent input is still in this tab."),
    ).toBeTruthy();
    const reads = () =>
      GET.mock.calls.filter(([path]) => path === "/api/threads/{thread_id}")
        .length;
    expect(reads()).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(reads()).toBe(2));
    expect(POST).not.toHaveBeenCalled();
    expect(draft.doc.getText("text").toString()).toBe("Keep this unsent input");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(
      screen.getByRole("link", { name: "New chat" }).getAttribute("href"),
    ).toBe("/");
  } finally {
    view.unmount();
    query.clear();
    draft.doc.destroy();
  }
});
