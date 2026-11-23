// @vitest-environment jsdom
import { i18n } from "./i18n";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "./transport/context";
import { createTransport, type Schema } from "./transport/client";
import { InboxPage } from "./inbox";
import { markdownPreview } from "./conversations/markdown-preview";

function item(
  id: string,
  kind: Schema<"InboxItem">["kind"],
  title: string,
): Schema<"InboxItem"> {
  return {
    item_id: id,
    thread_id: `thread-${id}`,
    tool_call_id: `call-${id}`,
    kind,
    title,
    message: `**${title}** details\n\n| A | B |\n| --- | --- |\n| 1 | 2 |`,
    created_at: "2026-11-23T10:00:00Z",
    thread_title: `Chat ${id}`,
  };
}

let patches: { id: string; body: unknown }[];
beforeEach(async () => {
  await i18n.changeLanguage("en");
  patches = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (request.method === "PATCH") {
        patches.push({
          id: url.pathname.split("/").pop()!,
          body: await request.json(),
        });
        return Response.json(item("x", "result", "x"));
      }
      if (url.pathname === "/api/inbox")
        return Response.json({
          items: [
            item("update", "result", "Fix is ready"),
            item("question", "needs_human", "Choose a timeout"),
          ],
          unread_count: 2,
        });
      return Response.json(url.pathname === "/api/projects" ? [] : {});
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mount() {
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queries}>
      <TransportContext value={createTransport("test", () => {})}>
        <MemoryRouter initialEntries={["/inbox"]}>
          <InboxPage />
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>,
  );
}

it("leads with questions, reads a row in place, and acts from the row", async () => {
  mount();
  const needs = await screen.findByRole("region", { name: "Needs you" });
  const question = within(needs).getByRole("button", {
    name: "Needs you, Choose a timeout, Unread",
  });
  expect(
    within(screen.getByRole("region", { name: "Updates" })).getByRole(
      "button",
      { name: "Result, Fix is ready, Unread" },
    ),
  ).toBeTruthy();
  expect(question.textContent).toContain(
    "Chat question · Choose a timeout details A B 1 2",
  );

  fireEvent.click(question);
  expect(question.getAttribute("aria-expanded")).toBe("true");
  expect(within(needs).getByRole("table")).toBeTruthy();
  expect(
    within(needs)
      .getByRole("link", { name: "Open in chat" })
      .getAttribute("href"),
  ).toBe("/threads/thread-question?inbox=question");
  await waitFor(() =>
    expect(patches).toContainEqual({ id: "question", body: { read: true } }),
  );

  fireEvent.click(
    within(screen.getByRole("region", { name: "Updates" })).getByRole(
      "button",
      { name: "Mark as done" },
    ),
  );
  await waitFor(() =>
    expect(patches).toContainEqual({ id: "update", body: { done: true } }),
  );
});

it("previews Markdown as one line of plain text", () => {
  expect(
    markdownPreview(
      "## Plan\n- **Keep** `order_items`\n- [Docs](https://x.test)\n\n| a | b |\n|---|:-:|\n| 1 | 2 |",
    ),
  ).toBe("Plan Keep order_items Docs a b 1 2");
});
