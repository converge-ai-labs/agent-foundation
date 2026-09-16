import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { initialConfig } from "./configuration";
import { AgentForm } from "./form";

vi.mock("../web/selection", () => ({ AgentSearchSelection: () => null }));
vi.mock("../memory/selection", () => ({ AgentMemorySelection: () => null }));
const memoryAvailability = vi.hoisted(() => ({ visible: true }));
vi.mock("../memory/availability", () => ({
  useMemoryProviders: () => memoryAvailability,
}));
vi.mock("./choices", () => ({
  useAgentChoices: () => ({
    isPending: false,
    data: {
      models: [
        { key: "research", name: "Research model", upstream_model: "upstream" },
      ],
      skills: [{ key: "sources", name: "Source verification" }],
      connections: [
        { id: "mcp_0123456789abcdef", name: "Web tools" },
        { id: "conn_0123456789abcdef", name: "Repository" },
      ],
    },
  }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "workspace" },
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
  }),
}));
beforeEach(() =>
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  ),
);
afterEach(() => {
  memoryAvailability.visible = true;
  cleanup();
  vi.unstubAllGlobals();
});

function editor(
  readonly = false,
  connectorTools: NonNullable<
    ReturnType<typeof initialConfig>["connection_tools"]
  > = [],
  memory?: ReturnType<typeof initialConfig>["memory"],
) {
  const submit = vi.fn();
  const initial = {
    ...initialConfig("Research"),
    memory,
    model: { model_key: "research" },
    instructions: "Check the evidence.",
    connection_tools: connectorTools,
    skills: [{ skill_key: "sources", version: 3 }],
    secret_requirements: [{ key: "research-token", required: true }],
  };
  render(
    <MemoryRouter>
      <AgentForm
        back="/agents"
        name="Research"
        primaryAction={<button type="button">Try agent</button>}
        identityAction={<button type="button">Edit agent details</button>}
        context={<button type="button">More agent actions</button>}
        initial={initial}
        version={7}
        pending={false}
        error={undefined}
        readonly={readonly}
        submit={submit}
      />
    </MemoryRouter>,
  );
  return { submit, initial };
}

it("edits capabilities inline and preserves pinned versions, hidden configuration and version evidence", async () => {
  const user = userEvent.setup(),
    { submit, initial } = editor();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("checkbox", { name: "Web tools" })).toBeNull();
  expect(
    (
      screen.getByRole("spinbutton", {
        name: "Pinned version",
      }) as HTMLInputElement
    ).value,
  ).toBe("3");
  await user.click(screen.getByRole("button", { name: "Add Connections" }));
  await user.click(screen.getByRole("checkbox", { name: "Web tools" }));
  await user.type(
    screen.getByRole("textbox", { name: "Tool names" }),
    "search, read",
  );
  await user.click(screen.getByRole("checkbox", { name: "Repository" }));
  await user.type(
    screen.getAllByRole("textbox", { name: "Tool names" })[1],
    "profile.read, search",
  );
  await user.click(
    screen.getAllByRole("checkbox", { name: "Load tools on demand" })[1],
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(screen.queryByRole("alert")?.textContent).toBeUndefined();
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      instructions: initial.instructions,
      skills: initial.skills,
      secret_requirements: initial.secret_requirements,
      connection_tools: [
        {
          connection_id: "mcp_0123456789abcdef",
          tools: ["search", "read"],
        },
        {
          connection_id: "conn_0123456789abcdef",
          tools: ["profile.read", "search"],
          defer_loading: true,
        },
      ],
    }),
    "Research",
    "",
    7,
  );
});

it("shows configuration as readable values for readers", () => {
  const { submit } = editor(true);
  expect(screen.queryByRole("button", { name: "Add Connections" })).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Remove Source verification" }),
  ).toBeNull();
  expect(
    screen.queryByRole("textbox", { name: "System instructions" }),
  ).toBeNull();
  expect(
    screen.getByRole("group", { name: "System instructions" }).textContent,
  ).toContain("Check the evidence.");
  expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
  expect(submit).not.toHaveBeenCalled();
});

it("requires saving the instruction draft before trial or agent management", async () => {
  const user = userEvent.setup();
  const { submit } = editor();
  await user.type(
    screen.getByRole("textbox", { name: "System instructions" }),
    " More detail.",
  );
  expect(
    screen.getByRole("button", { name: "Try agent" }).matches(":disabled"),
  ).toBe(true);
  for (const name of ["Edit agent details", "More agent actions"]) {
    expect(screen.getByRole("button", { name }).matches(":disabled")).toBe(
      true,
    );
  }
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      instructions: "Check the evidence. More detail.",
    }),
    "Research",
    "",
    7,
  );
});

it("distinguishes an empty Connector allowlist from all tools", async () => {
  const user = userEvent.setup(),
    { submit } = editor();
  await user.click(screen.getByRole("button", { name: "Add Connections" }));
  await user.click(screen.getByRole("checkbox", { name: "Repository" }));
  await user.click(screen.getByRole("checkbox", { name: "Select no tools" }));
  expect(
    (screen.getByRole("textbox", { name: "Tool names" }) as HTMLInputElement)
      .disabled,
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit.mock.calls[0][0].connection_tools[0].tools).toEqual([]);
  await user.click(screen.getByRole("checkbox", { name: "Select no tools" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit.mock.calls[1][0].connection_tools[0].tools).toBeNull();
});

it.each([
  { tools: null, label: "All tools" },
  { tools: [], label: "No tools" },
  { tools: ["profile.read"], label: "profile.read" },
])(
  "preserves read-only Connector tool selection: $label",
  async ({ tools, label }) => {
    const user = userEvent.setup();
    const { submit } = editor(true, [
      {
        connection_id: "conn_0123456789abcdef",
        tools,
        defer_loading: true,
      },
    ]);
    expect(screen.queryByRole("textbox", { name: "Tool names" })).toBeNull();
    expect(
      screen.getByRole("group", { name: "Tool names" }).textContent,
    ).toContain(label);
    for (const name of ["Select no tools", "Load tools on demand"]) {
      const checkbox = screen.getByRole("checkbox", { name });
      expect(checkbox.getAttribute("aria-disabled")).toBe("true");
      const checked = checkbox.getAttribute("aria-checked");
      await user.click(checkbox);
      expect(checkbox.getAttribute("aria-checked")).toBe(checked);
    }
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
    expect(submit).not.toHaveBeenCalled();
  },
);

it("hides unconfigured memory without changing the submitted configuration", async () => {
  memoryAvailability.visible = false;
  const user = userEvent.setup();
  const { submit } = editor();
  expect(screen.queryByRole("heading", { name: "Memory" })).toBeNull();
  await user.type(
    screen.getByRole("textbox", { name: "System instructions" }),
    " More detail.",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit.mock.calls[0][0].memory).toBeUndefined();
});
it("retains a saved memory section and values when its backend is unavailable", async () => {
  memoryAvailability.visible = false;
  const user = userEvent.setup();
  const memory = {
    provider_id: "memprov_0123456789abcdef",
    auto_recall: false,
    recall_threshold: 0,
  };
  const { submit } = editor(false, [], memory);
  expect(screen.getByRole("heading", { name: "Memory" })).toBeTruthy();
  await user.type(
    screen.getByRole("textbox", { name: "System instructions" }),
    " More detail.",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit.mock.calls[0][0].memory).toEqual(memory);
});
