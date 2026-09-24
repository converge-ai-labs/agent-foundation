// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ClearContext } from "./clear-context";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";

afterEach(cleanup);

function setup(
  POST = vi
    .fn()
    .mockResolvedValue({ data: { continuation_id: "b".repeat(64) } }),
) {
  const queries = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  const onPendingChange = vi.fn();
  const onCleared = vi.fn();
  const reconcile = vi.fn();
  function tree(continuationId = "a".repeat(64), disabled = false) {
    return (
      <QueryClientProvider client={queries}>
        <TransportContext value={{ client: { POST } } as unknown as Transport}>
          <ClearContext
            threadId="thread-one"
            continuationId={continuationId}
            disabled={disabled}
            onPendingChange={onPendingChange}
            onCleared={onCleared}
            reconcile={reconcile}
          />
        </TransportContext>
      </QueryClientProvider>
    );
  }
  return {
    ...render(tree()),
    tree,
    POST,
    queries,
    onPendingChange,
    onCleared,
    reconcile,
  };
}

it("confirms clearing, sends the reviewed continuation, and refreshes only after success", async () => {
  const { POST, onPendingChange, onCleared, reconcile, queries } = setup();
  fireEvent.click(screen.getByRole("button", { name: "Clear context" }));
  expect(screen.getByRole("dialog").textContent).toContain(
    "Chat history, files, conversation settings, and your unsent draft are kept",
  );
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(POST).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Clear context" }));
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Clear context",
    }),
  );
  await waitFor(() => expect(onCleared).toHaveBeenCalledOnce());
  expect(POST).toHaveBeenCalledExactlyOnceWith(
    "/api/threads/{thread_id}/clear-context",
    {
      params: { path: { thread_id: "thread-one" } },
      body: { expected_continuation_id: "a".repeat(64) },
    },
  );
  expect(onPendingChange.mock.calls).toEqual([[true], [false]]);
  expect(reconcile).toHaveBeenCalledOnce();
  expect(queries.getQueryData(["thread", "thread-one", "detail"])).toEqual({
    continuation_id: "b".repeat(64),
  });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("does not silently clear a newer context or allow clearing during activity", async () => {
  const { POST, rerender, tree } = setup();
  rerender(tree("a".repeat(64), true));
  expect(
    (screen.getByRole("button", { name: "Clear context" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  rerender(tree());
  fireEvent.click(screen.getByRole("button", { name: "Clear context" }));
  rerender(tree("b".repeat(64)));
  expect(screen.getByRole("alert").textContent).toContain(
    "conversation changed",
  );
  const confirm = within(screen.getByRole("dialog")).getByRole("button", {
    name: "Clear context",
  });
  expect((confirm as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(confirm);
  expect(POST).not.toHaveBeenCalled();
});

it("reports uncertain outcomes without claiming success or retrying automatically", async () => {
  const POST = vi.fn().mockRejectedValue(new TypeError("Connection lost"));
  const { onCleared, reconcile, onPendingChange } = setup(POST);
  fireEvent.click(screen.getByRole("button", { name: "Clear context" }));
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Clear context",
    }),
  );
  await screen.findByText(/context may already have been cleared/);
  expect(onCleared).not.toHaveBeenCalled();
  expect(POST).toHaveBeenCalledOnce();
  expect(reconcile).toHaveBeenCalledOnce();
  expect(onPendingChange).toHaveBeenLastCalledWith(false);
});
