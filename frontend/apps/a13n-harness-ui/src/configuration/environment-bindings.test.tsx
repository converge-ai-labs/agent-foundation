// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { TransportContext } from "../transport/context";
import { createTransport } from "../transport/client";
import {
  EnvironmentBindings,
  type EnvironmentBinding,
} from "./environment-bindings";
import { ForgetEnvironment } from "./forget-environment";
import { fullControl, readOnly } from "./environment-permissions";

let queries: QueryClient;
let requests: Request[];
let available: boolean;
let discovery: boolean;
let blocked: boolean;
const binding: EnvironmentBinding = {
  device_id: "device-build",
  alias: "build",
  working_directory: "/work",
};
function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
beforeEach(() => {
  queries = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  requests = [];
  available = true;
  discovery = true;
  blocked = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      const url = new URL(request.url);
      if (request.method === "DELETE")
        return blocked
          ? json(
              {
                error: {
                  code: "configuration_invalid",
                  message: "Configuration references are invalid.",
                },
              },
              400,
            )
          : json({ action: "deleted" });
      if (url.pathname === "/api/devices")
        return json([
          { id: "device-build", name: "Build machine", transport: "http" },
        ]);
      if (url.pathname === "/api/devices/device-build")
        return json({
          id: "device-build",
          name: "Build machine",
          transport: "http",
          available,
          directory_discovery: discovery,
          default_working_directory: available ? "/work" : null,
        });
      if (url.pathname === "/api/devices/device-build/directories") {
        const path = url.searchParams.get("path")!;
        return json({
          path,
          parent_path: path === "/work" ? "/" : "/work",
          entries:
            path === "/work" ? [{ name: "alpha", path: "/work/alpha" }] : [],
          next_offset: null,
        });
      }
      if (url.pathname === "/api/setup")
        return json({ suggested_project_path: "/local" });
      if (url.pathname === "/api/projects")
        return json([
          {
            project_id: "project-build",
            name: "Build project",
            roots: ["/local"],
            defaults: { environment_bindings: [binding] },
          },
        ]);
      if (url.pathname === "/api/configuration/sources")
        return json({
          sources: [
            {
              relative_path: "projects/build.yaml",
              resource_ids: ["project-build"],
            },
          ],
        });
      throw new Error(`Unexpected request: ${request.method} ${url}`);
    }),
  );
});
afterEach(() => {
  cleanup();
  queries.clear();
  vi.unstubAllGlobals();
});
function mount(component: React.ReactNode) {
  render(
    <MemoryRouter>
      <QueryClientProvider client={queries}>
        <TransportContext value={createTransport("fixture", () => {})}>
          {component}
        </TransportContext>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
function Fields({ initial = [] }: { initial?: EnvironmentBinding[] }) {
  const [bindings, setBindings] = useState(initial);
  const [defaultEnvironment, setDefault] = useState<string | null>("workspace");
  return (
    <>
      <EnvironmentBindings
        bindings={bindings}
        defaultEnvironment={defaultEnvironment}
        localRoots={["/local"]}
        onChange={(next, value) => {
          setBindings(next);
          setDefault(value);
        }}
      />
      <output data-testid="selection">
        {JSON.stringify({ bindings, defaultEnvironment })}
      </output>
    </>
  );
}
async function chooseDevice() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Add environment" }));
  await user.click(screen.getByRole("combobox", { name: "Device" }));
  await user.click(
    await screen.findByRole("option", { name: "Build machine" }),
  );
  await user.clear(screen.getByRole("textbox", { name: "Environment alias" }));
  await user.type(
    screen.getByRole("textbox", { name: "Environment alias" }),
    "build",
  );
  return user;
}
it("selects an explicit Device directory and preserves it when reopening", async () => {
  mount(<Fields />);
  const user = await chooseDevice();
  await user.click(
    await screen.findByRole("button", { name: "Browse directories" }),
  );
  await user.click(await screen.findByRole("button", { name: "alpha" }));
  await screen.findByText("No subdirectories.");
  expect(
    (
      screen.getByRole("textbox", {
        name: "Working directory",
      }) as HTMLInputElement
    ).value,
  ).toBe("");
  await user.click(
    screen.getByRole("button", { name: "Select this directory" }),
  );
  await user.click(screen.getByRole("button", { name: "Use environment" }));
  expect(JSON.parse(screen.getByTestId("selection").textContent!)).toEqual({
    bindings: [
      {
        ...binding,
        working_directory: "/work/alpha",
        permission_ceiling: { operations: fullControl },
      },
    ],
    defaultEnvironment: "workspace",
  });
  await user.click(screen.getByRole("button", { name: "Edit build" }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "Working directory",
      }) as HTMLInputElement
    ).value,
  ).toBe("/work/alpha");
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});
it("retains manual paths offline, retries observations, and respects disabled discovery", async () => {
  available = false;
  mount(<Fields />);
  const user = await chooseDevice();
  await screen.findByText(/Device unavailable/);
  await user.type(
    screen.getByRole("textbox", { name: "Working directory" }),
    "/known",
  );
  expect(
    screen.queryByRole("button", { name: "Browse directories" }),
  ).toBeNull();
  available = true;
  discovery = false;
  await user.click(screen.getByRole("button", { name: "Retry connection" }));
  await screen.findByText(/Directory browsing is disabled/);
  expect(
    (
      screen.getByRole("textbox", {
        name: "Working directory",
      }) as HTMLInputElement
    ).value,
  ).toBe("/known");
  await user.click(screen.getByRole("button", { name: "Use Device default" }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "Working directory",
      }) as HTMLInputElement
    ).value,
  ).toBe("/work");
  await user.click(screen.getByRole("button", { name: "Use environment" }));
  expect(
    JSON.parse(screen.getByTestId("selection").textContent!).bindings,
  ).toEqual([{ ...binding, permission_ceiling: { operations: fullControl } }]);
  expect(requests.some((request) => request.url.includes("/directories"))).toBe(
    false,
  );
});
it("defaults to full control and can save and reopen read-only access", async () => {
  mount(<Fields />);
  const user = await chooseDevice();
  const actions = screen.getByRole("combobox", { name: "Allowed actions" });
  expect(actions.textContent).toBe("Full control");
  await user.click(actions);
  await user.click(await screen.findByRole("option", { name: "Read only" }));
  await user.click(
    await screen.findByRole("button", { name: "Use Device default" }),
  );
  await user.click(screen.getByRole("button", { name: "Use environment" }));
  expect(
    JSON.parse(screen.getByTestId("selection").textContent!).bindings,
  ).toEqual([{ ...binding, permission_ceiling: { operations: readOnly } }]);
  await user.click(screen.getByRole("button", { name: "Edit build" }));
  expect(
    screen.getByRole("combobox", { name: "Allowed actions" }).textContent,
  ).toBe("Read only");
  await user.click(screen.getByRole("combobox", { name: "Allowed actions" }));
  await user.click(await screen.findByRole("option", { name: "Full control" }));
  await user.click(screen.getByRole("button", { name: "Use environment" }));
  expect(
    JSON.parse(screen.getByTestId("selection").textContent!).bindings,
  ).toEqual([{ ...binding, permission_ceiling: { operations: fullControl } }]);
  await user.click(screen.getByRole("button", { name: "Edit build" }));
  expect(
    screen.getByRole("combobox", { name: "Allowed actions" }).textContent,
  ).toBe("Full control");
});
it.each([
  undefined,
  { operations: ["environment.file.read_text"] },
  {
    operations: ["environment.computer.observe", "environment.computer.click"],
  },
  { operations: [] },
] satisfies EnvironmentBinding["permission_ceiling"][])(
  "keeps custom action ceilings when editing only a path: %j",
  async (ceiling) => {
    const initial: EnvironmentBinding = {
      ...binding,
      permission_ceiling: ceiling,
      expected_boundary: { sandbox: { mode: "disabled" }, egress: "inherit" },
      egress: { destinations: { mode: "allowlist", hosts: ["example.com"] } },
    };
    mount(<Fields initial={[initial]} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Edit build" }));
    expect(
      screen.getByRole("combobox", { name: "Allowed actions" }).textContent,
    ).toBe("Keep existing permissions");
    await user.clear(
      screen.getByRole("textbox", { name: "Working directory" }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "Working directory" }),
      "/other",
    );
    await user.click(screen.getByRole("button", { name: "Use environment" }));
    expect(
      JSON.parse(screen.getByTestId("selection").textContent!).bindings,
    ).toEqual([{ ...initial, working_directory: "/other" }]);
  },
);
it("forgets local configuration through the existing API without any Device connection", async () => {
  mount(
    <ForgetEnvironment
      path="devices/build.yaml"
      name="Build machine"
      resourceId="device-build"
    />,
  );
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: "Forget Build machine" }),
  );
  const link = await screen.findByRole("link", { name: "Build project" });
  expect(link.getAttribute("href")).toContain("projects%2Fbuild.yaml");
  blocked = true;
  await user.click(
    screen.getByRole("button", { name: "Forget configuration" }),
  );
  await screen.findByText("Configuration references are invalid.");
  expect(screen.getByRole("dialog")).toBeTruthy();
  blocked = false;
  await user.click(
    screen.getByRole("button", { name: "Forget configuration" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    requests
      .filter((request) => request.method === "DELETE")
      .map((request) => new URL(request.url).pathname),
  ).toEqual([
    "/api/configuration/sources/devices%2Fbuild.yaml",
    "/api/configuration/sources/devices%2Fbuild.yaml",
  ]);
  expect(
    requests.some((request) =>
      new URL(request.url).pathname.startsWith("/api/devices/"),
    ),
  ).toBe(false);
});
