// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { TransportContext } from "../transport/context";
import { createTransport } from "../transport/client";
import { DraftContext, SourceDocument } from "./sources";
import { EnvdRuntime } from "./envd";

vi.mock("./editor", () => ({ SourceEditor: () => null }));
let queries: QueryClient;
let requests: string[];
let ready: boolean;
let sandboxReady: boolean;
let running: number;
let installed: boolean;
let cachedVersions: string[];
let content: string;
let preflight: ((request: Request) => Promise<Response>) | undefined;
const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
beforeEach(() => {
  queries = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  requests = [];
  ready = true;
  sandboxReady = true;
  running = 0;
  installed = false;
  cachedVersions = [];
  preflight = undefined;
  content =
    '# Keep this comment\nschema_version: "1"\ndefaults: {environment_profile: environment-native}\ntools: {enable_codeact: false}\n';
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      requests.push(`${request.method} ${path}`);
      if (path === "/api/selectors")
        return json({
          agents: [],
          models: [],
          harness_plugins: [],
          environment_run_extensions: [],
          mcp_servers: [],
          environments: [
            {
              profile_id: "environment-review",
              name: "Review workspace",
              release_owned: false,
            },
          ],
        });
      if (path === "/api/envd")
        return json({
          host: "test-host",
          source: "managed",
          required_version: "1.2.3",
          installed,
          cached_versions: cachedVersions,
          phase: "idle",
          downloaded_bytes: 0,
          instances: running
            ? [
                {
                  device_id: "device-test",
                  sandbox: false,
                  computer_use: false,
                  running: true,
                  roots: [],
                },
              ]
            : [],
          readiness: [],
          computer_use: false,
          running_devices: running,
        });
      if (path === "/api/configuration/sources/config.yaml") {
        if (request.method === "PUT") content = (await request.json()).content;
        return json({
          content,
          content_available: true,
          writable: true,
          source_digest: content,
          resource_kind: "root",
          resource_ids: [],
        });
      }
      if (
        [
          "/api/envd/start",
          "/api/envd/restart",
          "/api/envd/reinstall",
        ].includes(path)
      ) {
        if (preflight) return preflight(request);
        running = ready ? 1 : 0;
        if (ready) {
          installed = true;
          cachedVersions = [...new Set([...cachedVersions, "1.2.3"])];
        }
        return json({
          profile_id: "environment-envd",
          ready,
          code: ready ? "ready" : "offline",
          message: ready ? "Runtime started" : "Download failed",
          instructions: [],
        });
      }
      if (path === "/api/environments/preflight") {
        return json({
          ready: sandboxReady,
          message: sandboxReady ? "Sandbox ready" : "Isolation unavailable",
          instructions: [],
        });
      }
      if (path === "/api/envd/stop") {
        running = 0;
        return new Response(null, { status: 204 });
      }
      throw new Error(`Unexpected ${request.method} ${path}`);
    }),
  );
});
afterEach(() => {
  cleanup();
  queries.clear();
  vi.unstubAllGlobals();
});
function mount(section: "execution" | "envd" | "runtime") {
  render(
    <MemoryRouter>
      <QueryClientProvider client={queries}>
        <TransportContext.Provider
          value={createTransport("test-key", () => undefined)}
        >
          <DraftContext.Provider value={new Map()}>
            {section === "runtime" ? (
              <EnvdRuntime />
            ) : (
              <SourceDocument path="config.yaml" embedded section={section} />
            )}
          </DraftContext.Provider>
        </TransportContext.Provider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
it("saves execution defaults through the source owner without preparing envd", async () => {
  mount("execution");
  const runtime = await screen.findByRole("combobox", { name: "Runtime" });
  await waitFor(() => expect(runtime.hasAttribute("disabled")).toBe(false));
  await userEvent.click(runtime);
  await userEvent.click(await screen.findByRole("option", { name: "envd" }));
  expect(
    screen
      .getByRole("link", { name: /Manage local runtime/ })
      .getAttribute("href"),
  ).toBe("/settings/environments?tab=runtime");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Saved", { exact: true });
  expect(content).toContain("environment-envd");
  expect(content).toContain("# Keep this comment");
  expect(content).toContain("enable_codeact: false");
  expect(requests.some((item) => item.startsWith("POST"))).toBe(false);
  expect(screen.queryByText("Installation details")).toBeNull();
});
it("selects a configured profile as the global execution default", async () => {
  mount("execution");
  await userEvent.click(
    await screen.findByRole("combobox", { name: "Runtime" }),
  );
  await userEvent.click(
    await screen.findByRole("option", { name: "Review workspace" }),
  );
  expect(screen.queryByRole("combobox", { name: "Permissions" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Saved", { exact: true });
  expect(content).toContain("environment-review");
  expect(requests.some((item) => item.startsWith("POST"))).toBe(false);
});
it("saves Host desktop capability without launching or changing execution defaults", async () => {
  mount("envd");
  const toggle = await screen.findByRole("switch", {
    name: "Desktop capability",
  });
  await waitFor(() => expect(toggle.hasAttribute("disabled")).toBe(false));
  await userEvent.click(toggle);
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Saved", { exact: true });
  expect(content).toContain("computer_use: true");
  expect(content).toContain("environment-native");
  expect(requests.some((item) => item.startsWith("POST"))).toBe(false);
});
it("saves collection cadence without starting or restarting a runtime", async () => {
  content +=
    "envd: {executable: /custom/envd, computer_use: true, monitoring_interval_seconds: 20}\n";
  mount("envd");
  const interval = await screen.findByRole("combobox", {
    name: "Metrics collection interval",
  });
  await waitFor(() => expect(interval.hasAttribute("disabled")).toBe(false));
  expect(interval.textContent).toContain("Every 20 seconds");
  await userEvent.click(interval);
  await userEvent.click(
    await screen.findByRole("option", { name: "Every 30 seconds" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Saved", { exact: true });
  expect(content).toContain("monitoring_interval_seconds: 30");
  expect(content).toContain("/custom/envd");
  expect(content).toContain("computer_use: true");
  expect(requests.some((item) => item.startsWith("POST"))).toBe(false);
});
it("starts envd only on explicit action and never publishes a default", async () => {
  mount("runtime");
  await screen.findByText("test-host");
  expect(requests.some((item) => item.startsWith("POST"))).toBe(false);
  await userEvent.click(
    screen.getByRole("button", { name: "Install & start" }),
  );
  await screen.findByText("Runtime and Sandbox ready.");
  expect(content).toContain("environment-native");
  expect(requests.filter((item) => item.startsWith("PUT"))).toEqual([]);
  expect(requests.filter((item) => item.startsWith("POST"))).toEqual([
    "POST /api/envd/start",
    "POST /api/environments/preflight",
  ]);
});
it("keeps Full Control running when the automatic Sandbox check fails", async () => {
  sandboxReady = false;
  mount("runtime");
  await userEvent.click(
    await screen.findByRole("button", { name: "Install & start" }),
  );
  await screen.findByText("Runtime ready. Sandbox: Isolation unavailable");
  expect(running).toBe(1);
  expect(content).toContain("environment-native");
  expect(requests).not.toContain("POST /api/envd/stop");
  sandboxReady = true;
  await userEvent.click(await screen.findByRole("button", { name: "Check" }));
  await screen.findByText("Runtime and Sandbox ready.");
});
it("updates to the required version when another release is cached", async () => {
  cachedVersions = ["1.2.2"];
  mount("runtime");
  await screen.findByText("Update required");
  expect(screen.queryByRole("button", { name: "Reinstall…" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Update & start" }));
  await screen.findByText("Runtime and Sandbox ready.");
  await screen.findByRole("button", { name: "Reinstall…" });
  expect(cachedVersions).toEqual(["1.2.2", "1.2.3"]);
  expect(requests.filter((item) => item.startsWith("POST"))).toEqual([
    "POST /api/envd/start",
    "POST /api/environments/preflight",
  ]);
  expect(content).toContain("environment-native");
});
it("retains defaults and permits retry after preparation failure", async () => {
  ready = false;
  mount("runtime");
  await userEvent.click(
    await screen.findByRole("button", { name: "Install & start" }),
  );
  await screen.findByText("Download failed");
  expect(content).toContain("environment-native");
  ready = true;
  await userEvent.click(
    screen.getByRole("button", { name: "Install & start" }),
  );
  await screen.findByText("Runtime and Sandbox ready.");
});
it("cancels preparation without publishing configuration", async () => {
  let signal: AbortSignal | undefined;
  preflight = (request) => {
    signal = request.signal;
    return new Promise((_resolve, reject) =>
      signal!.addEventListener("abort", () => reject(signal!.reason), {
        once: true,
      }),
    );
  };
  mount("runtime");
  await userEvent.click(
    await screen.findByRole("button", { name: "Install & start" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Cancel preparation" }),
  );
  await screen.findByText(/Preparation cancelled/);
  expect(signal?.aborted).toBe(true);
  expect(requests.filter((item) => item.startsWith("PUT"))).toEqual([]);
});
it("requires confirmation before stopping shared runtimes", async () => {
  running = 1;
  mount("runtime");
  await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
  await screen.findByRole("dialog", { name: "Stop all local envd runtimes?" });
  expect(requests).not.toContain("POST /api/envd/stop");
  await userEvent.click(
    screen.getByRole("button", { name: "Stop all runtimes" }),
  );
  await screen.findByText("Runtimes stopped.");
  expect(requests).toContain("POST /api/envd/stop");
});

it.each([
  ["Restart", "Restart runtime", "/api/envd/restart"],
  ["Reinstall…", "Reinstall & restart", "/api/envd/reinstall"],
])(
  "confirms %s before interrupting Sessions",
  async (trigger, confirm, path) => {
    running = 1;
    installed = true;
    mount("runtime");
    await userEvent.click(await screen.findByRole("button", { name: trigger }));
    await screen.findByRole("dialog");
    expect(requests).not.toContain(`POST ${path}`);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(requests).not.toContain(`POST ${path}`);
    await userEvent.click(screen.getByRole("button", { name: trigger }));
    await userEvent.click(screen.getByRole("button", { name: confirm }));
    await screen.findByText("Runtime and Sandbox ready.");
    expect(requests).toContain(`POST ${path}`);
    expect(content).toContain("environment-native");
  },
);
