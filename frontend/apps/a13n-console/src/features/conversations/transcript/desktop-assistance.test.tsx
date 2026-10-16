import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DesktopAssistance } from "./desktop-assistance";

const { get, post, viewers, resolved } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  viewers: [] as Array<
    EventTarget & { viewOnly: boolean; disconnect: ReturnType<typeof vi.fn> }
  >,
  resolved: vi.fn(),
}));
vi.mock("../../../auth/context", () => ({
  useClient: () => ({ workspace: () => ({ GET: get, POST: post }) }),
}));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (value: string) => value }),
}));
vi.mock("@novnc/novnc/lib/rfb", () => ({
  default: {
    default: class extends EventTarget {
      viewOnly = false;
      scaleViewport = false;
      disconnect = vi.fn();
      constructor() {
        super();
        viewers.push(this);
      }
    },
  },
}));
const view = {
  id: "assistance",
  run_id: "run",
  tool_call_id: "help",
  environment_id: "env",
  mount: "workspace",
  reason: "Please choose a file",
  state: "awaiting_user",
  epoch: 0,
  controller_id: null,
  deadline: "2099-01-01",
  lease_expires_at: null,
  failure: null,
  outcome: null,
};
const reply = (value: object) => ({ data: value, response: new Response() });
function start() {
  get.mockResolvedValue(reply(view));
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const component = render(
    <QueryClientProvider client={cache}>
      <DesktopAssistance runId="run" callId="help" onResolved={resolved} />
    </QueryClientProvider>,
  );
  return { cache, ...component };
}
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  viewers.length = 0;
});

it("keeps input disabled until a confirmed control grant connects; never caches credentials", async () => {
  const user = userEvent.setup();
  const { cache, unmount } = start();
  post.mockResolvedValue(
    reply({
      assistance: { ...view, state: "human_control", epoch: 1 },
      mode: "control",
      websocket_url: "wss://desktop.example/?token=secret",
      password: "private",
      expires_in_seconds: 60,
    }),
  );
  await user.click(await screen.findByRole("button", { name: "Take control" }));
  await waitFor(() => expect(viewers).toHaveLength(1));
  expect(viewers[0].viewOnly).toBe(true);
  viewers[0].dispatchEvent(new Event("connect"));
  await screen.findByText("You control this desktop. The agent is paused.");
  expect(viewers[0].viewOnly).toBe(false);
  expect(
    JSON.stringify(
      cache
        .getQueryCache()
        .getAll()
        .map((item) => item.state.data),
    ),
  ).not.toContain("private");
  expect(resolved).not.toHaveBeenCalled();
  unmount();
  expect(viewers[0].viewOnly).toBe(true);
  expect(viewers[0].disconnect).toHaveBeenCalled();
  expect(post).toHaveBeenLastCalledWith(
    "/api/v1/desktop-assistance/{assistance_id}/disconnect",
    expect.objectContaining({ body: expect.objectContaining({ epoch: 1 }) }),
  );
});

it("read-only grants never enable input and completion waits for confirmed server cleanup", async () => {
  const user = userEvent.setup();
  start();
  post.mockResolvedValueOnce(
    reply({
      assistance: view,
      mode: "view",
      websocket_url: "wss://desktop.example/",
      password: "private",
      expires_in_seconds: 60,
    }),
  );
  await user.click(
    await screen.findByRole("button", { name: "Open read-only desktop" }),
  );
  await waitFor(() => expect(viewers).toHaveLength(1));
  viewers[0].dispatchEvent(new Event("connect"));
  await screen.findByText("Read-only desktop");
  expect(viewers[0].viewOnly).toBe(true);
  let complete!: (value: object) => void;
  post.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  await user.click(screen.getByRole("button", { name: "Finish assistance" }));
  expect(viewers[0].disconnect).toHaveBeenCalled();
  expect(resolved).not.toHaveBeenCalled();
  const outcome = { assistance_id: "assistance", outcome: "completed" };
  complete(reply({ ...view, state: "ready_to_resume", outcome }));
  await waitFor(() => expect(resolved).toHaveBeenCalledWith(outcome));
});

it("does not resolve the deferred call when revocation fails", async () => {
  const user = userEvent.setup();
  start();
  post.mockRejectedValueOnce(new Error("Cleanup unconfirmed"));
  await user.click(
    await screen.findByRole("button", { name: "Finish assistance" }),
  );
  await screen.findByText("Cleanup unconfirmed");
  expect(resolved).not.toHaveBeenCalled();
});
