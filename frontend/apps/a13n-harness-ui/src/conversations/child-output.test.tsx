// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import { ApiError, type Schema, type Transport } from "../transport/client";
import { ChildSavedOutputs } from "./child-output";

afterEach(cleanup);

it("reads the latest child result inline and joins exact-source windows without an event log", async () => {
  const target: Schema<"SavedOutputTarget"> = {
    producing_thread_id: "child-one",
    source_id: "b".repeat(64),
    location: { kind: "child_text", execution_id: "exec-one", activity: null },
  };
  const first = {
    target,
    text: "First paragraph.\n\n",
    offset: 0,
    total_characters: 38,
    next_offset: 18,
  };
  const GET = vi.fn(async () => ({ data: { outputs: [first] } }));
  const POST = vi.fn(async () => ({
    data: {
      target,
      text: "Final paragraph.",
      offset: 18,
      total_characters: 34,
      next_offset: null,
    },
  }));
  const queries = new QueryClient();
  render(
    <QueryClientProvider client={queries}>
      <TransportContext
        value={{ client: { GET, POST } } as unknown as Transport}
      >
        <ChildSavedOutputs
          threadId="parent"
          rootThreadId="root"
          executionId="exec-one"
          fallback={<p>Waiting for saved output</p>}
        />
      </TransportContext>
    </QueryClientProvider>,
  );
  await screen.findByText("First paragraph.");
  expect(screen.queryByText("Saved child output and comments")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Load more result" }));
  await screen.findByText("Final paragraph.");
  expect(POST.mock.calls[0]).toEqual([
    "/api/threads/{thread_id}/saved-output",
    expect.objectContaining({
      params: { path: { thread_id: "root" }, query: { offset: 18 } },
      body: target,
    }),
  ]);
  expect(screen.queryByRole("button", { name: "Load more result" })).toBeNull();
});

it("keeps observed output until a saved child result becomes available", async () => {
  const GET = vi
    .fn()
    .mockRejectedValueOnce(
      new ApiError("No checkpoint", 400, "comment_source_unavailable"),
    )
    .mockResolvedValue({ data: { outputs: [] } });
  const queries = new QueryClient();
  render(
    <QueryClientProvider client={queries}>
      <TransportContext value={{ client: { GET } } as unknown as Transport}>
        <ChildSavedOutputs
          threadId="root"
          rootThreadId="root"
          executionId="exec-one"
          fallback={<p>Observed answer</p>}
        />
      </TransportContext>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(1));
  expect(screen.getByText("Observed answer")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  await queries.invalidateQueries({
    queryKey: ["child-saved-output", "root", "exec-one"],
  });
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(2));
  expect(screen.getByText("Observed answer")).toBeTruthy();
});
