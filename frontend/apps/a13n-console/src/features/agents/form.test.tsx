import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { initialConfig } from "./configuration";
import { AgentForm } from "./form";

vi.mock("../search/selection", () => ({ AgentSearchSelection: () => null }));
vi.mock("./choices", () => ({
  useAgentChoices: () => ({
    isPending: false,
    data: {
      models: [
        { key: "research", name: "Research model", upstream_model: "upstream" },
      ],
      skills: [{ key: "sources", name: "Source verification" }],
      mcp: [{ id: "mcp_0123456789abcdef", name: "Web tools" }],
      connectors: [{ id: "conn_0123456789abcdef", name: "Repository" }],
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
  cleanup();
  vi.unstubAllGlobals();
});

function editor(readonly = false) {
  const submit = vi.fn();
  const initial = {
    ...initialConfig("Research"),
    model: { model_key: "research" },
    instructions: "Check the evidence.",
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
  await user.click(screen.getByRole("button", { name: "Add MCP connections" }));
  await user.click(screen.getByRole("checkbox", { name: "Web tools" }));
  await user.type(
    screen.getByRole("textbox", { name: "Tool names" }),
    "search, read",
  );
  await user.click(screen.getByRole("button", { name: "Add Connectors" }));
  await user.click(screen.getByRole("checkbox", { name: "Repository" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(screen.queryByRole("alert")?.textContent).toBeUndefined();
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      instructions: initial.instructions,
      skills: initial.skills,
      secret_requirements: initial.secret_requirements,
      mcp_tools: [
        {
          mcp_connection_id: "mcp_0123456789abcdef",
          tools: ["search", "read"],
        },
      ],
      connector_tools: [{ connector_connection_id: "conn_0123456789abcdef" }],
    }),
    "Research",
    "",
    7,
  );
});

it("keeps inline capability controls inert for readers", async () => {
  const user = userEvent.setup(),
    { submit } = editor(true);
  await user.click(screen.getByRole("button", { name: "Add MCP connections" }));
  expect(screen.queryByRole("checkbox", { name: "Web tools" })).toBeNull();
  expect(
    (
      screen.getByRole("button", {
        name: "Remove Source verification",
      }) as HTMLButtonElement
    ).matches(":disabled"),
  ).toBe(true);
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
