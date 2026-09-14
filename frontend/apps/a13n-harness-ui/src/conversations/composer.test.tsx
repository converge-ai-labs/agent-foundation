// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Y from "yjs";
import { Composer, ComposerDrafts } from "./composer";
import { ThreadDraft, encode, values } from "./draft";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";

beforeEach(() => {
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => ({
      left: 0,
      top: 0,
      right: 0,
      bottom: 0,
      width: 0,
      height: 0,
    }),
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("reserves an upload before joining and resolves it after the initial draft identity arrives", async () => {
  const draft = new ThreadDraft();
  vi.spyOn(draft, "connect").mockReturnValue({
    presence: () => {},
    close: () => {},
  });
  let resolve!: (response: Response) => void;
  const request = new Promise<Response>((done) => {
    resolve = done;
  });
  const attachment = {
    attachment_id: "attachment-one",
    name: "notes.txt",
    size: 5,
    media_type: "text/plain",
  };
  const transport = {
    fetch: vi.fn().mockReturnValue(request),
    client: { GET: vi.fn().mockResolvedValue({ data: attachment }) },
  } as unknown as Transport;
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={query}>
      <TransportContext value={transport}>
        <ComposerDrafts value={new Map([["thread-one", draft]])}>
          <Composer
            threadId="thread-one"
            activity={{ state: "inactive" } as Schema<"RootActivityView">}
            canRun
            profile={{ display_name: "Alice", color: "#2563eb" }}
            unauthorized={() => {}}
            reconcile={() => {}}
          />
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>,
  );
  fireEvent.change(view.container.querySelector('input[type="file"]')!, {
    target: {
      files: [new File(["notes"], "notes.txt", { type: "text/plain" })],
    },
  });
  await screen.findByRole("button", {
    name: "notes.txt · not ready",
  });
  expect(
    (
      screen.getByRole("button", {
        name: "Send",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  act(() => {
    draft.receive({
      draft_id: "draft-one",
      participant_id: "participant-one",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    });
    // Typing after the reserved token while upload is in flight is not replaced.
    draft.doc
      .getText("text")
      .insert(draft.doc.getText("text").length, " after");
    resolve(
      new Response(JSON.stringify(attachment), {
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  await screen.findByRole("button", { name: "notes.txt" });
  await waitFor(() =>
    expect(values(draft.doc)).toEqual({
      prompt: " after",
      attachment_ids: ["attachment-one"],
    }),
  );
  act(() =>
    draft.receive({
      draft_id: "draft-one",
      participant_id: "participant-one",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    }),
  );
  const captured = draft.capture();
  expect(captured.parts).toEqual([
    { attachment_id: "attachment-one" },
    " after",
  ]);
  captured.doc.destroy();
  query.clear();
});
