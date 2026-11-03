import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { useAgentChoices } from "../choices";
import { ToolSearchSection } from "./tool-search";
import { buildDraftConfig, useAgentDraft } from "./draft";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/\{\{(\w+)\}\}/g, (_, name) =>
        String(values?.[name] ?? name),
      ),
  }),
}));
vi.mock("../toolsets", () => ({
  useToolsetCatalog: () => ({
    data: {
      items: [
        {
          key: "files",
          default_enabled: true,
          tools: [{ key: "view", default_enabled: true }],
        },
        {
          key: "execution",
          default_enabled: false,
          platform: true,
          tools: [{ key: "get_trace", default_enabled: true }],
        },
      ],
    },
  }),
}));
afterEach(cleanup);
const id = "con_0123456789abcdef0123";
const choices = {
  data: {
    connections: [
      { id, name: "Knowledge", config: { url: "https://mcp.test" } },
    ],
  },
} as ReturnType<typeof useAgentChoices>;

it("preserves custom grouping behind a disclosure", async () => {
  const submit = vi.fn();
  function Editor() {
    const draft = useAgentDraft({
      model: "test",
      connection_tools: [{ connection_id: id }],
      tool_search: { groups: {} },
    });
    return (
      <>
        <ToolSearchSection draft={draft} choices={choices} readOnly={false} />
        <button
          onClick={() => {
            const result = buildDraftConfig(draft, undefined, (key) => key);
            if (result.ok) submit(result.config);
          }}
        >
          Save
        </button>
      </>
    );
  }
  const user = userEvent.setup();
  render(<Editor />);
  expect(screen.queryByRole("button", { name: "Add group" })).toBeNull();
  await user.click(screen.getByRole("button", { name: /Advanced grouping/ }));
  await user.click(screen.getByRole("button", { name: "Add group" }));
  await user.clear(screen.getByLabelText("Group name"));
  await user.type(screen.getByLabelText("Group name"), "knowledge");
  await user.type(
    screen.getByLabelText("Group description"),
    "Search team knowledge",
  );
  await user.click(screen.getByRole("checkbox", { name: "Knowledge" }));
  await user.click(screen.getByRole("checkbox", { name: "Files" }));
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      connection_tools: [{ connection_id: id }],
      tool_search: {
        config: { mode: "proxy" },
        groups: {
          knowledge: {
            description: "Search team knowledge",
            connections: [id],
            capabilities: ["files"],
            plugins: [],
            platform: false,
          },
        },
      },
    }),
  );
});

it("allows dormant platform discovery without enabling platform tools", async () => {
  function Editor({ enabled }: { enabled: boolean }) {
    const draft = useAgentDraft({
      model: "test",
      toolsets: { execution: { enabled } },
      tool_search: {
        config: { mode: "proxy" },
        groups: { tools: { description: "Tools" } },
      },
    });
    return (
      <ToolSearchSection draft={draft} choices={choices} readOnly={false} />
    );
  }
  const { rerender } = render(<Editor key="off" enabled={false} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Advanced grouping/ }));
  await user.click(screen.getByRole("button", { name: /^tools/ }));
  const source = () => screen.getByRole("checkbox", { name: /Platform tools/ });
  expect(source().hasAttribute("data-disabled")).toBe(false);
  rerender(<Editor key="on" enabled />);
  await user.click(screen.getByRole("button", { name: /Advanced grouping/ }));
  await user.click(screen.getByRole("button", { name: /^tools/ }));
  expect(source().hasAttribute("data-disabled")).toBe(false);
});

it("enables automatic discovery in one choice and saves without opening groups", async () => {
  const submit = vi.fn();
  function Editor() {
    const draft = useAgentDraft({
      model: "test",
      connection_tools: [{ connection_id: id }],
    });
    return (
      <>
        <ToolSearchSection draft={draft} choices={choices} readOnly={false} />
        <button
          onClick={() =>
            submit(buildDraftConfig(draft, undefined, (key) => key))
          }
        >
          Save
        </button>
      </>
    );
  }
  const user = userEvent.setup();
  render(<Editor />);
  await user.hover(
    screen.getByRole("button", { name: "About tool availability" }),
  );
  expect(
    await screen.findByText(/reads their parameters when needed/),
  ).toBeTruthy();
  await user.unhover(
    screen.getByRole("button", { name: "About tool availability" }),
  );
  await user.click(screen.getByRole("combobox", { name: "Mode" }));
  await user.click(
    await screen.findByRole("option", { name: "Discover tools on demand" }),
  );
  expect(screen.queryByRole("button", { name: "Add group" })).toBeNull();
  expect(screen.queryByLabelText("Group name")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(submit).toHaveBeenLastCalledWith(
    expect.objectContaining({
      ok: true,
      config: expect.objectContaining({
        tool_search: { config: { mode: "proxy" }, automatic: {} },
      }),
    }),
  );
  for (const name of ["Computer", "Browser", "Platform tools", "MCP tools"]) {
    expect(
      screen.getByRole("button", { name: `Provide ${name} directly` }),
    ).toBeTruthy();
  }
  expect(
    screen.queryByRole("button", { name: "Provide Files directly" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Add tool source" }));
  await user.click(await screen.findByRole("menuitem", { name: "Files" }));
  await user.click(
    screen.getByRole("button", { name: "Provide Computer directly" }),
  );
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(submit).toHaveBeenLastCalledWith(
    expect.objectContaining({
      ok: true,
      config: expect.objectContaining({
        tool_search: {
          config: { mode: "proxy" },
          automatic: { capabilities: ["browser", "files"] },
        },
      }),
    }),
  );
  await user.click(screen.getByRole("button", { name: /Advanced grouping/ }));
  await user.click(screen.getByRole("combobox", { name: "Grouping" }));
  await user.click(
    await screen.findByRole("option", { name: "Custom groups" }),
  );
  expect(screen.queryByLabelText("Group name")).toBeNull();
  await user.click(screen.getByRole("button", { name: /^files Files/ }));
  expect(screen.getByLabelText("Group name")).toHaveProperty("value", "files");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(submit).toHaveBeenLastCalledWith(
    expect.objectContaining({
      ok: true,
      config: expect.objectContaining({
        tool_search: {
          config: { mode: "proxy" },
          groups: {
            browser: {
              description: "Use browser tools.",
              capabilities: ["browser"],
            },
            files: { description: "Use files tools.", capabilities: ["files"] },
            platform: {
              description: "Manage selected platform resources.",
              platform: true,
            },
            "connection-1": {
              description: `Tools from connection ${id}.`,
              connections: [id],
            },
          },
        },
      }),
    }),
  );
});

it("shows empty discovery and reveals invalid custom fields without changing read-only drafts", async () => {
  function Editor({ error }: { error?: Error }) {
    const draft = useAgentDraft({
      model: "test",
      tool_search: { groups: { empty: { description: "Empty" } } },
    });
    return (
      <>
        <ToolSearchSection
          draft={draft}
          choices={choices}
          readOnly
          error={error}
        />
        <span>{draft.dirty ? "Changed" : "Saved"}</span>
      </>
    );
  }
  const { rerender } = render(<Editor />);
  expect(screen.getByRole("status").textContent).toContain(
    "No sources selected",
  );
  expect(screen.queryByLabelText("Group name")).toBeNull();
  rerender(<Editor error={new Error("Select a source")} />);
  expect(screen.getByLabelText("Group name").textContent).toContain("empty");
  expect(screen.queryByRole("textbox", { name: "Group name" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Add group" })).toBeNull();
  expect(screen.getByText("Saved")).toBeTruthy();
});
