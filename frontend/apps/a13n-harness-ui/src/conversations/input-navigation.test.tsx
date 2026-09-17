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
import { createRef } from "react";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { InputNavigation } from "./input-navigation";
import { previewInput, type LocalInput } from "./local-input";

afterEach(cleanup);

it("loads the complete lightweight directory and excludes steer and duplicate local inputs", async () => {
  const GET = vi
    .fn()
    .mockResolvedValueOnce({
      data: {
        continuation_id: "C1",
        turns: [{ turn_id: "first", preview: "First input" }],
        next_cursor: "next",
      },
    })
    .mockResolvedValueOnce({
      data: {
        continuation_id: "C1",
        turns: [{ turn_id: "second", preview: "Second input" }],
        next_cursor: null,
      },
    });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const select = vi.fn();
  const view = render(
    <QueryClientProvider client={client}>
      <TransportContext value={{ client: { GET } } as unknown as Transport}>
        <InputNavigation
          threadId="one"
          continuation="C1"
          reader={createRef()}
          revision={1}
          onSelect={select}
          localInputs={[
            ...(
              [
                "preparing",
                "pending",
                "unknown",
                "rejected",
              ] as LocalInput["state"][]
            ).map((state) => ({
              id: state,
              action: "send" as const,
              state,
              parts: previewInput(state, ["Unconfirmed input"]),
            })),
            {
              id: "first",
              action: "send",
              state: "accepted",
              parts: previewInput("first", ["First input"]),
            },
            {
              id: "steer",
              action: "steer",
              state: "accepted",
              parts: previewInput("steer", ["Not in directory"]),
            },
          ]}
        />
      </TransportContext>
    </QueryClientProvider>,
  );
  const second = await screen.findByRole("button", {
    name: "Input 2: Second input",
  });
  expect(
    screen.getAllByRole("button", { name: "Input 1: First input" }),
  ).toHaveLength(1);
  expect(screen.queryByText("Not in directory")).toBeNull();
  expect(
    screen.queryByRole("button", { name: /Unconfirmed input/ }),
  ).toBeNull();
  fireEvent.click(second);
  expect(select).toHaveBeenCalledWith("second");
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(2));
  expect(GET.mock.calls.every(([path]) => path.endsWith("/inputs"))).toBe(true);
  expect(GET.mock.calls[1][1].params.query.cursor).toBe("next");
  view.unmount();
  client.clear();
});
