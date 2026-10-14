// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { createClient, type Client } from "../../service-client";
import { overriddenFields, RunAgentDialog } from "./run-agent";
import { fixtureRun, fixtureRunAgent } from "./transcript/fixture";

let client: Client;
let cache: QueryClient;
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace", settings: {} },
    organization: { id: "org" },
    basePath: "/workspace/design",
    can: () => true,
  }),
}));

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const executed = fixtureRunAgent();
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const url = new URL(new Request(input, init).url);
      // The Service applies the Run's overrides; the Console shows its result.
      if (url.pathname.endsWith("/runs/run_2/agent"))
        return Response.json({
          ...executed,
          config: { ...executed.config, instructions: "Only this run." },
        });
      return Response.json({ items: [], next_cursor: null });
    },
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
  vi.unstubAllGlobals();
});

it("names only the fields a Run's overrides change", () => {
  expect(
    overriddenFields(
      fixtureRun({
        options: { overrides: { instructions: "Only this run.", model: null } },
      }),
    ),
  ).toEqual(["instructions"]);
  expect(overriddenFields(fixtureRun())).toEqual([]);
});

it("shows the configuration a Run executed, read-only, with its overrides marked", async () => {
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <RunAgentDialog
          run={fixtureRun({
            options: { overrides: { instructions: "Only this run." } },
          })}
          open
          onOpenChange={() => {}}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText("Only this run.")).toBeTruthy();
  expect(within(dialog).getByText("v2")).toBeTruthy();
  const instructions = within(dialog).getByRole("region", {
    name: "Instructions",
  });
  expect(within(instructions).getByText("Overridden")).toBeTruthy();
  const model = within(dialog).getByRole("region", { name: "Model" });
  expect(within(model).queryByText("Overridden")).toBeNull();
  // Nothing in it can be edited or saved.
  expect(
    within(dialog).queryByRole("textbox", { name: "System instructions" }),
  ).toBeNull();
  expect(
    within(dialog).queryByRole("button", { name: "Save changes" }),
  ).toBeNull();
});
