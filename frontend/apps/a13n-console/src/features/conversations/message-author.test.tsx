// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import type { ReactNode } from "react";
import { createClient, type Client } from "../../service-client";
import type { Schema } from "../../shared/api";
import { SessionAuthorsProvider } from "./message-authors";
import { MessageAuthor } from "./message-author";
import { GuidanceMessage, UserMessage } from "./transcript/user-message";
import { DebugRunSection } from "./transcript/debug/run-section";
import {
  fixtureRun,
  fixtureThread,
  fixtureTimeline,
} from "./transcript/fixture";

let client: Client, cache: QueryClient;
let failed = false;
const requests: URL[] = [];
const records = new Map<string, Schema["MessageAuthor"]>();
vi.mock("../../auth/context", () => ({
  useClient: () => client,
  useAuth: () => ({ data: { user: { value: { id: "usr_me" } } } }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace/design",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(options?.[name] ?? "")),
    i18n: { resolvedLanguage: "en" },
  }),
}));
function record(
  entry: string,
  id: string,
  name: string,
  email: string | null = `${id}@example.com`,
  kind = "user",
) {
  const author: Schema["MessageAuthor"] = {
    entry_id: entry,
    principal_id: id,
    submitted_at: "2026-10-09T08:05:00Z",
    principal: { id, name, email, kind, status: "active", image_url: null },
  };
  records.set(entry, author);
  return author;
}
beforeEach(() => {
  requests.length = 0;
  records.clear();
  failed = false;
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const url = new URL(new Request(input, init).url);
      if (url.pathname.endsWith("/message-authors")) {
        requests.push(url);
        if (failed)
          return Response.json(
            { code: "unavailable", message: "Try again", details: {} },
            { status: 503 },
          );
        return Response.json({
          items: url.searchParams
            .getAll("entry_id")
            .flatMap((id) => (records.has(id) ? [records.get(id)] : [])),
        });
      }
      return Response.json({ items: [], next_cursor: null });
    },
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
});
function show(children: ReactNode) {
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <SessionAuthorsProvider sessionId="sess_current">
          {children}
        </SessionAuthorsProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("attributes Chat input and steer separately, and exposes a keyboard-accessible identity card", async () => {
  record("inb_initial", "usr_me", "Morgan");
  record("inb_steer", "usr_peer", "Alex", "alex@example.com");
  show(
    <>
      <UserMessage
        request={{ kind: "message", input: {}, text: "Original request" }}
        entryId="inb_initial"
        principalId="usr_me"
      />
      <GuidanceMessage text="Use the revised numbers" sourceId="inb_steer" />
    </>,
  );
  const mine = await screen.findByRole("button", {
    name: "Show author: Morgan",
  });
  const peer = await screen.findByRole("button", { name: "Show author: Alex" });
  expect(mine.textContent).toContain("You");
  expect(peer.textContent).not.toContain("You");
  expect(requests).toHaveLength(1);
  expect(requests[0]!.pathname).toBe(
    "/api/v1/sessions/sess_current/message-authors",
  );
  expect(requests[0]!.searchParams.getAll("entry_id")).toEqual([
    "inb_initial",
    "inb_steer",
  ]);
  const user = userEvent.setup();
  peer.focus();
  await user.keyboard("{Enter}");
  await screen.findByText("alex@example.com");
  await user.click(screen.getByRole("button", { name: "Copy email" }));
  expect(await navigator.clipboard.readText()).toBe("alex@example.com");
  await user.click(screen.getByRole("button", { name: "Copy principal ID" }));
  expect(await navigator.clipboard.readText()).toBe("usr_peer");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(document.activeElement).toBe(peer));
});

it("uses a queued entry's submission time when Debug renders its later Run", async () => {
  record("inb_queue", "usr_peer", "Alex");
  show(
    <DebugRunSection
      run={fixtureRun({
        source_entry_id: "inb_queue",
        principal_id: "usr_peer",
        created_at: "2026-10-09T09:00:00Z",
      })}
      thread={fixtureThread()}
      timeline={fixtureTimeline()}
      index={2}
    />,
  );
  const author = await screen.findByRole("button", {
    name: "Show author: Alex",
  });
  expect(author.parentElement!.querySelector("time")?.dateTime).toBe(
    "2026-10-09T08:05:00Z",
  );
  expect(author.textContent).not.toContain("You");
});

it("disambiguates equal names, identifies service accounts, and never calls an unknown sender You", async () => {
  record("inb_a", "usr_me", "Alex", "me@example.com");
  record("inb_b", "usr_peer", "Alex", "peer@example.com");
  record("inb_bot", "sa_bot", "Automation", null, "service_account");
  show(
    <>
      <MessageAuthor entryId="inb_a" />
      <MessageAuthor entryId="inb_b" />
      <MessageAuthor entryId="inb_bot" />
      <MessageAuthor entryId="inb_gone" principalId="usr_unknown123" />
      <GuidanceMessage text="Legacy steer" />
    </>,
  );
  const people = await screen.findAllByRole("button", {
    name: "Show author: Alex",
  });
  expect(people[0]!.textContent).toContain("me@example.com");
  expect(people[1]!.textContent).toContain("peer@example.com");
  expect(
    screen.getByRole("button", { name: "Show author: Automation" }).textContent,
  ).toContain("Service account");
  const unknown = screen.getAllByRole("button", { name: "Show author: User" });
  expect(unknown).toHaveLength(2);
  for (const button of unknown) expect(button.textContent).not.toContain("You");
  expect(unknown[0]!.textContent).toContain("known123");
});

it("batches a long history and reuses lookup results when the same entries render twice", async () => {
  for (let n = 0; n < 105; n++) record(`inb_${n}`, "usr_peer", "Alex");
  show(
    <>
      {Array.from({ length: 105 }, (_, n) => (
        <div key={n}>
          <MessageAuthor entryId={`inb_${n}`} />
          <MessageAuthor entryId={`inb_${n}`} />
        </div>
      ))}
    </>,
  );
  await waitFor(() =>
    expect(
      screen.getAllByRole("button", { name: "Show author: Alex" }),
    ).toHaveLength(210),
  );
  expect(
    requests.map((url) => url.searchParams.getAll("entry_id").length),
  ).toEqual([100, 5]);
});

it("keeps the message readable when author lookup fails and retries from the card", async () => {
  failed = true;
  record("inb_a", "usr_peer", "Alex");
  show(
    <UserMessage
      request={{ kind: "message", input: {}, text: "Still readable" }}
      entryId="inb_a"
      principalId="usr_peer"
    />,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Show author: User" }));
  const retry = await screen.findByRole("button", { name: "Retry" });
  expect(screen.getByText("Still readable")).toBeTruthy();
  failed = false;
  await user.click(retry);
  expect(
    await screen.findByRole("button", { name: "Show author: Alex" }),
  ).toBeTruthy();
});
