// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { BotWorkNotes, ThreadBotNote } from "./bot-notes";
import { createTransport } from "./transport/client";
import { TransportContext } from "./transport/context";

vi.mock("./bot", () => ({
  useBot: () => ({ data: { enabled: true, state: { paused: true } } }),
}));
vi.mock("./conversations/queries", () => ({
  useThread: () => ({
    data: { thread: { configuration: { agent_source: { kind: "agent" } } } },
  }),
}));
vi.mock("./conversations/message-text", () => ({
  MessageText: ({ text }: { text: string }) => <div>{text}</div>,
}));
let queries: QueryClient;
let transport: ReturnType<typeof createTransport>;
afterEach(() => {
  cleanup();
  queries.clear();
  transport.close();
  vi.unstubAllGlobals();
});
function mount(view: React.ReactNode) {
  const requests: string[] = [];
  let content = "Full handoff with verified references.";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      requests.push(`${request.method} ${path}`);
      if (path === "/api/bot/notes")
        return Response.json({
          notes: content
            ? [
                {
                  thread_id: "thread-work",
                  title: "Checkout reliability",
                  project_id: "project-atlas",
                  summary: "Short handoff.",
                  updated_at: "2026-11-12T01:00:00Z",
                  archived: false,
                },
              ]
            : [],
          next_offset: null,
        });
      if (path === "/api/bot/notes/thread-work")
        return Response.json({ thread_id: "thread-work", content });
      if (path === "/api/projects")
        return Response.json([
          { project_id: "project-atlas", name: "Atlas", roots: [] },
        ]);
      return Response.json({});
    }),
  );
  queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  transport = createTransport("test-only-key", () => {});
  render(
    <QueryClientProvider client={queries}>
      <TransportContext.Provider value={transport}>
        <MemoryRouter>{view}</MemoryRouter>
      </TransportContext.Provider>
    </QueryClientProvider>,
  );
  return {
    requests,
    clear: () => {
      content = "";
    },
  };
}
it("reads previews, expands on demand, and refreshes a cleared slot without mutation", async () => {
  const fixture = mount(<BotWorkNotes />);
  const link = await screen.findByRole("link", {
    name: "Checkout reliability",
  });
  expect(link.getAttribute("href")).toBe("/threads/thread-work");
  expect(await screen.findByText("Atlas")).toBeTruthy();
  expect(screen.getByText("Short handoff.")).toBeTruthy();
  expect(fixture.requests).not.toContain("GET /api/bot/notes/thread-work");
  const expand = screen.getByRole("button", { name: "Read full note" });
  fireEvent.click(expand);
  expect(
    await screen.findByText("Full handoff with verified references."),
  ).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "Show less" })
      .getAttribute("aria-expanded"),
  ).toBe("true");
  fixture.clear();
  act(() => {
    void queries.invalidateQueries({ queryKey: ["bot-notes"] });
  });
  expect(await screen.findByText("No work needs follow-up yet.")).toBeTruthy();
  expect(fixture.requests.every((request) => request.startsWith("GET "))).toBe(
    true,
  );
});
it("keeps the own Bot Note separate from private notes and shows paused follow-up", async () => {
  const fixture = mount(<ThreadBotNote threadId="thread-work" />);
  expect(screen.getByRole("heading", { name: "Bot Note" })).toBeTruthy();
  expect(screen.getByText("Automation paused")).toBeTruthy();
  expect(
    await screen.findByText("Full handoff with verified references."),
  ).toBeTruthy();
  expect(
    screen.getByText(
      "Shared handoff with Bot, separate from this chat's private working notes.",
    ),
  ).toBeTruthy();
  fixture.clear();
  act(() => {
    void queries.invalidateQueries({ queryKey: ["bot-note", "thread-work"] });
  });
  expect(await screen.findByText("No Bot Note.")).toBeTruthy();
  expect(fixture.requests.every((request) => request.startsWith("GET "))).toBe(
    true,
  );
});
