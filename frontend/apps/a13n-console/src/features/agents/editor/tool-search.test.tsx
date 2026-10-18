import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { useAgentChoices } from "../choices";
import { ToolSearchSection } from "./tool-search";
import { buildDraftConfig, useAgentDraft } from "./draft";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
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

it("selects native discovery, edits a group, and preserves the connection's loading setting", async () => {
  const submit = vi.fn();
  function Editor() {
    const draft = useAgentDraft({
      model: "test",
      connection_tools: [{ connection_id: id, defer_loading: true }],
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
  await user.click(screen.getByRole("combobox", { name: "Discovery mode" }));
  await user.click(
    await screen.findByRole("option", { name: "Native tool search" }),
  );
  await user.click(
    screen.getByRole("combobox", { name: "Native search strategy" }),
  );
  await user.click(await screen.findByRole("option", { name: "BM25" }));
  await user.click(screen.getByRole("button", { name: "Add group" }));
  await user.clear(screen.getByLabelText("Group name"));
  await user.type(screen.getByLabelText("Group name"), "knowledge");
  await user.type(
    screen.getByLabelText("Group description"),
    "Search team knowledge",
  );
  await user.click(screen.getByRole("checkbox", { name: "Knowledge" }));
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      connection_tools: [{ connection_id: id, defer_loading: true }],
      tool_search: {
        config: { mode: "native", strategy: "bm25" },
        groups: {
          knowledge: {
            description: "Search team knowledge",
            connections: [id],
            plugins: [],
            platform: false,
          },
        },
      },
    }),
  );
});
