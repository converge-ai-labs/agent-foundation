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
  expect(
    screen
      .getByRole("checkbox", { name: "Source verification" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  expect(
    (
      screen.getByRole("spinbutton", {
        name: "Pinned version",
      }) as HTMLInputElement
    ).value,
  ).toBe("3");
  await user.click(screen.getByRole("checkbox", { name: "Web tools" }));
  await user.type(
    screen.getByRole("textbox", { name: "Tool names" }),
    "search, read",
  );
  await user.click(screen.getByRole("checkbox", { name: "Repository" }));
  await user.click(screen.getByRole("button", { name: "Expand instructions" }));
  await user.click(
    screen.getByRole("button", { name: "Collapse instructions" }),
  );
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
  await user.click(screen.getByRole("checkbox", { name: "Web tools" }));
  expect(
    screen
      .getByRole("checkbox", { name: "Web tools" })
      .getAttribute("aria-checked"),
  ).toBe("false");
  expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
  expect(submit).not.toHaveBeenCalled();
});
