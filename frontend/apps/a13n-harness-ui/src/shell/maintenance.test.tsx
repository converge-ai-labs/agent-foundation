// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { TransportContext } from "../transport/context";
import { createTransport, type Schema } from "../transport/client";
import { MaintenanceBanner, MaintenanceSettings } from "./maintenance";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function mount(initial: Schema<"MaintenanceView">) {
  let state = initial;
  const writes: { path: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (request.method === "POST") {
        const body = await request.text();
        writes.push({ path, body: body ? JSON.parse(body) : null });
        state = {
          enabled: true,
          phase: path.endsWith("prepare") ? "paused" : "idle",
          can_cancel: path.endsWith("prepare"),
        };
      }
      return new Response(JSON.stringify(state), {
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queries}>
      <TransportContext.Provider
        value={createTransport("fixture-key", () => {})}
      >
        <MemoryRouter>
          <MaintenanceBanner />
          <MaintenanceSettings />
        </MemoryRouter>
      </TransportContext.Provider>
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), writes };
}
it("requires confirmation, explains normal shutdown, and cancels preparation without replay", async () => {
  const { user, writes } = mount({ enabled: true, phase: "idle" });
  await user.click(
    await screen.findByRole("button", { name: "Prepare server update" }),
  );
  expect(writes).toHaveLength(0);
  const dialog = screen.getByRole("dialog");
  await user.click(
    within(dialog).getByRole("button", { name: "Prepare update" }),
  );
  await waitFor(() =>
    expect(screen.getByRole("status").textContent).toContain("normal shutdown"),
  );
  expect(screen.getByRole("status").textContent).toContain("Do not force-kill");
  expect(writes.map((item) => item.path)).toEqual(["/api/maintenance/prepare"]);
  await user.click(screen.getByRole("button", { name: "Cancel preparation" }));
  await screen.findByRole("button", { name: "Prepare server update" });
  expect(writes.map((item) => item.path)).toEqual([
    "/api/maintenance/prepare",
    "/api/maintenance/cancel",
  ]);
});
it("only discards a blocked handoff after confirming the prior instance stopped", async () => {
  const { user, writes } = mount({
    enabled: true,
    phase: "blocked",
    message: "Final checkpoint failed.",
    can_dismiss: true,
  });
  await user.click(
    await screen.findByRole("button", { name: "Dismiss handoff" }),
  );
  expect(writes).toHaveLength(0);
  expect(
    screen.queryByRole("button", { name: "Prepare server update" }),
  ).toBeNull();
  await user.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Previous instance stopped — dismiss",
    }),
  );
  await screen.findByRole("button", { name: "Prepare server update" });
  expect(writes).toEqual([
    {
      path: "/api/maintenance/dismiss",
      body: { previous_instance_stopped: true },
    },
  ]);
});
