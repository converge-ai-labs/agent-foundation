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
import { InboxNavigation, InboxPage } from "./inbox";
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
          unread_by_group: { needs_you: 1, updates: 1 },
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
          <InboxNavigation />
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

it("marks only the selected section read, refreshes counts, and keeps items open", async () => {
  const url = "/api/inbox/mark-read";
  const submitted: unknown[] = [];
  let fail = true;
  let counts = { needs_you: 1, updates: 3 };
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const target = new URL(request.url);
    if (target.pathname === url && request.method === "POST") {
      const { group } =
        (await request.json()) as Schema<"InboxMarkReadRequest">;
      submitted.push(group);
      if (fail) {
        fail = false;
        return new Response(null, { status: 500 });
      }
      counts = { ...counts, [group]: 0 };
      return new Response(null, { status: 204 });
    }
    const done = target.searchParams.get("state") === "done";
    return Response.json({
      items: done
        ? []
        : [
            {
              ...item("question", "needs_human", "Choose a timeout"),
              read_at: counts.needs_you ? null : "2026-11-23T10:01:00Z",
            },
            {
              ...item("update", "result", "Fix is ready"),
              read_at: counts.updates ? null : "2026-11-23T10:01:00Z",
            },
          ],
      next_cursor: done ? null : "update",
      unread_count: counts.needs_you + counts.updates,
      unread_by_group: counts,
    });
  });
  mount();

  const updates = within(
    await screen.findByRole("region", { name: "Updates" }),
  );
  const needs = within(screen.getByRole("region", { name: "Needs you" }));
  const updatesAction = updates.getByRole("button", {
    name: "Mark all as read",
  }) as HTMLButtonElement;
  const needsAction = needs.getByRole("button", {
    name: "Mark all as read",
  }) as HTMLButtonElement;
  expect(updates.getByRole("status").textContent).toBe("3 unread");
  expect(screen.getByRole("link", { name: "Inbox, 4 unread" })).toBeTruthy();
  fireEvent.click(updatesAction);
  expect((await updates.findByRole("alert")).textContent).toContain(
    "Could not mark this section as read.",
  );
  expect(needs.queryByRole("alert")).toBeNull();
  expect(updates.getByRole("status").textContent).toBe("3 unread");

  updatesAction.focus();
  fireEvent.click(updatesAction);
  await waitFor(() =>
    expect(updates.getByRole("status").textContent).toBe("All read"),
  );
  expect(updatesAction.getAttribute("aria-disabled")).toBe("true");
  expect(document.activeElement).toBe(updatesAction);
  fireEvent.click(updatesAction);
  expect(
    updates.getByRole("button", { name: "Result, Fix is ready" }),
  ).toBeTruthy();
  expect(
    needs.getByRole("button", { name: "Needs you, Choose a timeout, Unread" }),
  ).toBeTruthy();
  expect(screen.getByRole("link", { name: "Inbox, 1 unread" })).toBeTruthy();

  fireEvent.click(needsAction);
  await waitFor(() =>
    expect(needs.getByRole("status").textContent).toBe("All read"),
  );
  expect(needsAction.getAttribute("aria-disabled")).toBe("true");
  expect(
    needs.getByRole("button", { name: "Needs you, Choose a timeout" }),
  ).toBeTruthy();
  expect(screen.getByRole("link", { name: "Inbox" })).toBeTruthy();
  expect(submitted).toEqual(["updates", "updates", "needs_you"]);
  expect(patches).toEqual([]);

  fireEvent.click(screen.getByText("Done", { exact: true }));
  expect(await screen.findByText("Nothing done yet")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Mark all as read" })).toBeNull();
});

it("keeps pagination and bulk reading from interrupting each other", async () => {
  let finishBulk!: () => void;
  let finishPaging!: () => void;
  const bulk = new Promise<void>((resolve) => {
    finishBulk = resolve;
  });
  const nextPage = new Promise<void>((resolve) => {
    finishPaging = resolve;
  });
  const recent = item("recent", "result", "Recent update");
  const older = item("older", "result", "Earlier update");
  let readAt: string | null = null;
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (request.method === "POST") {
      await bulk;
      readAt = "2026-11-23T10:01:00Z";
      return new Response(null, { status: 204 });
    }
    const before = url.searchParams.get("before");
    if (before) await nextPage;
    return Response.json({
      items: [{ ...(before ? older : recent), read_at: readAt }],
      next_cursor: before ? null : recent.item_id,
      unread_count: readAt ? 0 : 2,
      unread_by_group: { needs_you: 0, updates: readAt ? 0 : 2 },
    });
  });
  mount();

  const action = (await screen.findByRole("button", {
    name: "Mark all as read",
  })) as HTMLButtonElement;
  const more = screen.getByRole("button", {
    name: "Load more",
  }) as HTMLButtonElement;
  fireEvent.click(action);
  await waitFor(() => expect(more.disabled).toBe(true));
  finishBulk();
  await waitFor(() => expect(more.disabled).toBe(false));
  expect(action.getAttribute("aria-disabled")).toBe("true");

  fireEvent.click(more);
  await waitFor(() => expect(more.disabled).toBe(true));
  finishPaging();
  expect(
    await screen.findByRole("button", { name: "Result, Earlier update" }),
  ).toBeTruthy();
});

it("previews Markdown as one line of plain text", () => {
  expect(
    markdownPreview(
      "## Plan\n- **Keep** `order_items`\n- [Docs](https://x.test)\n\n| a | b |\n|---|:-:|\n| 1 | 2 |",
    ),
  ).toBe("Plan Keep order_items Docs a b 1 2");
});
