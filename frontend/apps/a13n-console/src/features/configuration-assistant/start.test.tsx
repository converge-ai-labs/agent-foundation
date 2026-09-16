import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { ConfigurationStart } from "./start";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function Location() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}
function setup() {
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/readiness")
      ? { ready: true }
      : {
          items: [
            {
              id: "empty",
              root_thread_id: "empty-thread",
              title: null,
              has_runs: false,
              updated_at: "2026-09-16T07:00:00Z",
            },
            {
              id: "active",
              root_thread_id: "active-thread",
              title: "Build a support agent",
              has_runs: true,
              updated_at: "2026-09-16T07:01:00Z",
            },
          ],
          next_cursor: null,
        },
    response: new Response(),
  }));
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <MemoryRouter
        initialEntries={[
          "/workspace/test/configuration/new?agent=agt_test&revision=arev_test",
        ]}
      >
        <ConfigurationStart />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}
it("shows meaningful history and creates nothing before the first message is sent", async () => {
  const user = setup();
  expect(await screen.findByText("Not started")).toBeTruthy();
  expect(
    screen
      .getByRole("link", { name: /Build a support agent/ })
      .getAttribute("href"),
  ).toContain("active-thread");
  await user.type(
    screen.getByRole("textbox", { name: "Message" }),
    "Build a support agent",
  );
  expect(http.POST).not.toHaveBeenCalled();
});
it("retains the draft and input key when first-input submission fails, then navigates after retry", async () => {
  const user = setup();
  http.POST.mockResolvedValueOnce({
    data: { root_thread_id: "new-thread" },
    response: new Response(),
  })
    .mockRejectedValueOnce(new Error("Connection lost"))
    .mockResolvedValueOnce({
      data: { thread_id: "new-thread", run_id: "new-run" },
      response: new Response(),
    });
  await screen.findByText("Not started");
  await user.type(
    screen.getByRole("textbox", { name: "Message" }),
    "Build a support agent",
  );
  const submit = screen.getByRole("button", {
    name: "Start configuration conversation",
  });
  await user.click(submit);
  await screen.findByText("Connection lost");
  expect(http.POST).toHaveBeenCalledTimes(2);
  expect(http.POST.mock.calls[0]![1].body).toEqual({
    target_agent_id: "agt_test",
    source: { selector: "explicit", revision_id: "arev_test" },
  });
  expect(
    (screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement)
      .value,
  ).toBe("Build a support agent");
  await user.click(submit);
  await waitFor(() =>
    expect(screen.getByTestId("location").textContent).toBe(
      "/workspace/test/configuration-threads/new-thread?run=new-run",
    ),
  );
  expect(http.POST).toHaveBeenCalledTimes(3);
  expect(http.POST.mock.calls[1]).toEqual(http.POST.mock.calls[2]);
  expect(http.POST.mock.calls[2]![1].body.input.content).toEqual([
    { type: "text", text: "Build a support agent" },
  ]);
});
