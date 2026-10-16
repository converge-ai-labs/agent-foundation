// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  act,
  within,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import type { Transport, Schema } from "../transport/client";
import { EnvironmentMonitor, MonitorContent } from "./environment-monitor";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
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
function setup(dialog = false, history: Schema<"HistoryPoint">[] = []) {
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
              history,
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

it("refreshes automatically, pauses browser polling and allows manual refresh", async () => {
  vi.useFakeTimers();
  const { get } = setup();
  await act(() => vi.advanceTimersByTimeAsync(100));
  get.mockClear();
  await act(() => vi.advanceTimersByTimeAsync(10000));
  expect(
    get.mock.calls.some(
      ([path]) => path === "/api/environment-monitoring/{target_id}",
    ),
  ).toBe(true);
  fireEvent.change(screen.getByRole("combobox", { name: "Auto-refresh" }), {
    target: { value: "0" },
  });
  get.mockClear();
  await act(() => vi.advanceTimersByTimeAsync(60000));
  expect(get).not.toHaveBeenCalled();
  expect(screen.getByText("Refresh paused")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Refresh observations" }));
  await act(() => vi.advanceTimersByTimeAsync(100));
  expect(get).toHaveBeenCalledTimes(2);
});

it("shares zoom and cursor across charts, pins history through refresh and returns to live", async () => {
  const now = Math.floor(Date.now() / 60000);
  vi.spyOn(Date, "now").mockReturnValue((now + 0.5) * 60000);
  const history = Array.from({ length: 15 }, (_, i) => ({
    minute: now - 14 + i,
    segment: 1,
    cpu_busy_percent: { average: i, peak: i + 1 },
    memory_used_bytes: { average: 1024 * i, peak: 1024 * (i + 1) },
  }));
  const { queries } = setup(false, history);
  await screen.findByText("Monitoring disabled");
  const cpu = screen.getByRole("group", {
    name: "System CPU interactive chart",
  });
  const memory = screen.getByRole("group", {
    name: "System memory used interactive chart",
  });
  // JSDOM has no layout or native pointer capture.
  vi.stubGlobal("PointerEvent", MouseEvent);
  vi.spyOn(cpu, "getBoundingClientRect").mockReturnValue({
    left: 0,
    width: 700,
  } as DOMRect);
  Object.defineProperty(cpu, "setPointerCapture", { value: vi.fn() });
  fireEvent.pointerDown(cpu, { clientX: 250, button: 0 });
  fireEvent.pointerMove(cpu, { clientX: 650 });
  fireEvent.pointerUp(cpu, { clientX: 650 });
  expect(
    screen.getByRole<HTMLSelectElement>("combobox", { name: "Time range" })
      .value,
  ).toBe("custom");
  expect(screen.getByText("Historical range")).toBeTruthy();
  const cpuSection = screen.getByRole("region", { name: "System CPU history" });
  const memorySection = screen.getByRole("region", {
    name: "System memory used history",
  });
  const axes = () =>
    [cpu, memory].map(
      (chart) =>
        chart.parentElement!.querySelector("div:last-child")!.textContent,
    );
  expect(axes()[0]).toBe(axes()[1]);
  const pinned = axes();
  await act(async () => {
    await queries.invalidateQueries({ queryKey: ["environment-metrics"] });
  });
  expect(axes()).toEqual(pinned);
  fireEvent.keyDown(cpu, { key: "ArrowLeft" });
  expect(within(cpuSection).getByText("12.0%")).toBeTruthy();
  expect(within(memorySection).getByText("12.0 KiB")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Back to live" }));
  expect(
    screen.getByRole<HTMLSelectElement>("combobox", { name: "Time range" })
      .value,
  ).toBe("15");
  expect(screen.getByText("Live · auto-refreshing")).toBeTruthy();
  fireEvent.change(screen.getByRole("combobox", { name: "Time range" }), {
    target: { value: "60" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(screen.getByText("Historical range")).toBeTruthy();
  fireEvent.change(screen.getByRole("combobox", { name: "Device" }), {
    target: { value: "Test" },
  });
  await screen.findByText("Offline");
  expect(
    screen.getByRole<HTMLSelectElement>("combobox", { name: "Time range" })
      .value,
  ).toBe("15");
  vi.unstubAllGlobals();
});
