import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ToolExposureSection, ToolExposureFields } from "./tool-exposure";
import { buildDraftConfig, useAgentDraft } from "./draft";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);
const id = "con_0123456789abcdef0123";

it("edits native search and source exposure without changing selection or permission", async () => {
  const submit = vi.fn();
  function Editor() {
    const draft = useAgentDraft({
      model: "test",
      connection_tools: [
        { connection_id: id, permission: "ask", tools: ["lookup"] },
      ],
    });
    return (
      <>
        <ToolExposureSection draft={draft} readOnly={false} />
        <ToolExposureFields
          value={draft.connections[0]}
          onChange={(patch) =>
            draft.setConnections((current) => [{ ...current[0], ...patch }])
          }
        />
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
  await user.click(
    screen.getByRole("combobox", { name: "Progressive search" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Provider-native search" }),
  );
  await user.click(
    screen.getByRole("combobox", { name: "Native search strategy" }),
  );
  await user.click(await screen.findByRole("option", { name: "BM25" }));
  await user.click(
    screen.getByRole("combobox", { name: "Default tool exposure" }),
  );
  await user.click(
    await screen.findByRole("option", {
      name: "Progressive — discover when needed",
    }),
  );
  await user.type(
    screen.getByLabelText("Toolset purpose"),
    "Search team knowledge",
  );
  await user.click(screen.getByText("Per-tool overrides", { exact: false }));
  await user.type(screen.getByLabelText("Exact tool name"), "lookup");
  await user.click(screen.getByRole("button", { name: "Add override" }));
  await user.click(screen.getByRole("combobox", { name: "lookup" }));
  await user.click(await screen.findByRole("option", { name: "Direct" }));
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      progressive_search: "native",
      progressive_search_strategy: "bm25",
      connection_tools: [
        {
          connection_id: id,
          tools: ["lookup"],
          permission: "ask",
          exposure: "progressive",
          description: "Search team knowledge",
          tool_exposure: { lookup: "direct" },
        },
      ],
    }),
  );
});

it("keeps read-only source policies immutable", () => {
  render(
    <ToolExposureFields
      value={{
        exposure: "progressive",
        description: "Lookup",
        tool_exposure: { lookup: "direct" },
      }}
      readOnly
      onChange={vi.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: "Add override" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Toolset purpose" })).toBeNull();
  expect(screen.getByText("Lookup")).toBeTruthy();
});
