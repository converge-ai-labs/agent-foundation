// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
        turns: [
          {
            turn_id: "first",
            preview: "First input",
            output_preview: "First saved answer",
          },
        ],
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
  fireEvent.focus(screen.getByRole("button", { name: "Input 1: First input" }));
  expect(await screen.findByText("First saved answer")).toBeTruthy();
  fireEvent.click(second);
  expect(select).toHaveBeenCalledWith("second");
  fireEvent.click(screen.getByRole("button", { name: "Input history" }));
  expect(await screen.findByText("Output: First saved answer")).toBeTruthy();
  fireEvent.click(screen.getByText("Output: First saved answer"));
  expect(select).toHaveBeenLastCalledWith("first");
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(2));
  expect(GET.mock.calls.every(([path]) => path.endsWith("/inputs"))).toBe(true);
  expect(GET.mock.calls[1][1].params.query.cursor).toBe("next");
  view.unmount();
  client.clear();
});

it("quickly previews each question and answer while the wave follows hover and keyboard focus", async () => {
  const user = userEvent.setup();
  const GET = vi.fn();
  const client = new QueryClient();
  const select = vi.fn();
  const view = render(
    <QueryClientProvider client={client}>
      <TransportContext value={{ client: { GET } } as unknown as Transport}>
        <InputNavigation
          threadId="one"
          reader={createRef()}
          revision={1}
          onSelect={select}
          localInputs={Array.from({ length: 8 }, (_, index) => ({
            id: `input-${index}`,
            action: "send",
            state: "accepted",
            parts: previewInput(`input-${index}`, [`Question ${index + 1}`]),
          }))}
        />
      </TransportContext>
    </QueryClientProvider>,
  );
  const ticks = screen.getAllByRole("button", { name: /^Input \d+:/ });
  expect(ticks).toHaveLength(8);
  await user.hover(ticks[3]);
  expect(ticks.map((tick) => tick.getAttribute("data-proximity"))).toEqual([
    "3",
    "2",
    "1",
    "0",
    "1",
    "2",
    "3",
    "4",
  ]);
  // The old default tooltip delay is 600ms; navigation previews should be quick.
  expect(
    await screen.findByText("Question 4", {}, { timeout: 400 }),
  ).toBeTruthy();
  expect(screen.getByText("Output")).toBeTruthy();
  expect(screen.getByText("No saved output yet")).toBeTruthy();
  await user.hover(ticks[4]);
  expect(ticks[4].getAttribute("data-proximity")).toBe("0");
  expect(await screen.findByText("Question 5")).toBeTruthy();
  await user.unhover(ticks[4]);
  expect(ticks.every((tick) => !tick.hasAttribute("data-proximity"))).toBe(
    true,
  );
  await waitFor(() => expect(screen.queryByText("Output")).toBeNull());

  await user.tab();
  expect(document.activeElement).toBe(ticks[0]);
  expect(ticks[0].getAttribute("data-proximity")).toBe("0");
  expect(await screen.findByText("Question 1")).toBeTruthy();
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByText("Output")).toBeNull());
  await user.keyboard("{Enter}");
  expect(select).toHaveBeenCalledWith("input-0");
  expect(ticks[0].getAttribute("aria-current")).toBe("location");
  await user.tab();
  expect(document.activeElement).toBe(ticks[1]);
  expect(ticks[1].getAttribute("data-proximity")).toBe("0");
  // Hover and keyboard navigation never fetch execution history.
  expect(GET).not.toHaveBeenCalled();
  view.unmount();
  client.clear();
});
