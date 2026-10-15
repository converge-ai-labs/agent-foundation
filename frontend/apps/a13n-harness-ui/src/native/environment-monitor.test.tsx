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
import { TransportContext } from "../transport/context";
import type { Transport, Schema } from "../transport/client";
import { EnvironmentMonitor, MonitorContent } from "./environment-monitor";

afterEach(cleanup);
const environment = (
  id: string,
  status: Schema<"EnvironmentObservation">["status"],
): Schema<"EnvironmentObservation"> => ({
  id,
  name: id,
  transport: "http",
  roots: [],
  status,
  system_info: { os: "linux" },
});
function setup(dialog = false) {
  const environments = [
    environment("Build", "disabled"),
    environment("Test", "offline"),
  ];
  const get = vi.fn(
    async (
      path: string,
      options?: { params?: { path?: { target_id: string } } },
    ) => ({
      response: new Response(),
      data:
        path === "/api/environment-monitoring"
          ? { environments, omitted: 0 }
          : {
              environment: environments.find(
                (item) => item.id === options?.params?.path?.target_id,
              ),
              history: [],
            },
    }),
  );
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={queries}>
      <TransportContext
        value={{ client: { GET: get } } as unknown as Transport}
      >
        {dialog ? <EnvironmentMonitor /> : <MonitorContent />}
      </TransportContext>
    </QueryClientProvider>,
  );
  return { get, queries };
}
it("selects Device identity without reusing another Device's sample", async () => {
  const { get } = setup();
  await screen.findByText("Monitoring disabled");
  expect(screen.getByText("linux")).toBeTruthy();
  fireEvent.change(screen.getByRole("combobox", { name: "Device" }), {
    target: { value: "Test" },
  });
  await screen.findByText("Offline");
  expect(screen.queryByText("Monitoring disabled")).toBeNull();
  expect(
    get.mock.calls.some(
      ([, options]) => options?.params?.path?.target_id === "Test",
    ),
  ).toBe(true);
  expect(screen.getAllByText("—").length).toBeGreaterThan(0);
});
it("starts browser queries on open and removes their observers on close", async () => {
  const { get, queries } = setup(true);
  expect(get).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Environment monitor" }));
  await screen.findByText("Monitoring disabled");
  fireEvent.click(
    screen.getByRole("button", { name: "Close environment monitor" }),
  );
  await waitFor(() =>
    expect(
      queries
        .getQueryCache()
        .findAll()
        .every((query) => query.getObserversCount() === 0),
    ).toBe(true),
  );
});
