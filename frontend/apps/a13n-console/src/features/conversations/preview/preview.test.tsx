// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { createClient, type Client } from "../../../service-client";
import { SessionLayout, ThreadLayout } from "../page";
import { RunPage } from "../transcript";
import { createFakeService, type FakeService } from "./fake-service";
import { previewScenario } from "./scenario";

let client: Client;
vi.mock("../../../auth/context", () => ({
  useClient: () => client,
  revalidateSession: () => undefined,
}));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/preview",
    workspace: { id: "wsp_preview", key: "preview" },
    can: (action: string) => action !== "notification.subscribe",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

let service: FakeService | undefined;
afterEach(() => {
  cleanup();
  client.close();
  service?.close();
});

it("renders the Release Bot run waiting on an approval from the fake Service", async () => {
  const scenario = previewScenario();
  service = createFakeService(scenario);
  client = createClient({
    baseUrl: "https://preview.example",
    auth: { type: "session", csrfToken: "preview" },
    fetch: service.fetch,
  });
  const { sessionId, threadId, runId } = scenario.entry;
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter
        initialEntries={[
          `/workspace/preview/sessions/${sessionId}/threads/${threadId}/runs/${runId}`,
        ]}
      >
        <Routes>
          <Route
            path="/workspace/:workspaceKey/sessions/:sessionId"
            element={<SessionLayout />}
          >
            <Route path="threads/:threadId" element={<ThreadLayout />}>
              <Route path="runs/:runId" element={<RunPage />} />
            </Route>
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  // The Run's own request, from the Run resource.
  expect(
    await screen.findByText("Write the release marker for 2.4.0"),
  ).toBeTruthy();
  // The deferred tool call, projected from the Run stream.
  expect(await screen.findByText("shell_exec")).toBeTruthy();
  // The approval the Run is waiting for, from its pending actions.
  expect(await screen.findByText("Waiting for your response")).toBeTruthy();
  expect(
    await screen.findByRole("button", { name: "Approve once" }),
  ).toBeTruthy();
  // The approval names the command the Run is asking to run.
  expect(
    screen.getAllByText("echo approved > releases/2.4.0/approved-marker.txt")
      .length,
  ).toBeGreaterThan(0);
  expect(service.unhandled).toEqual([]);
  cache.clear();
});

it("replays the Run stream from the origin and resumes from Last-Event-ID", async () => {
  const scenario = previewScenario();
  service = createFakeService(scenario);
  client = createClient({
    baseUrl: "https://preview.example",
    auth: { type: "session" },
    fetch: service.fetch,
  });
  const runId = scenario.entry.runId;
  const cursors: string[] = [];
  for await (const entry of client.streamRun(runId)) cursors.push(entry.cursor);
  expect(cursors.length).toBeGreaterThan(10);

  const resumed: string[] = [];
  for await (const entry of client.streamRun(runId, {
    after: cursors[cursors.length - 4],
  }))
    resumed.push(entry.cursor);
  expect(resumed).toEqual(cursors.slice(-3));
});
