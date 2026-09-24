import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { expect, it, vi } from "vitest";
import { initialConfig } from "../configuration";
import { useAgentDraft, type AgentDraft } from "./draft";
import { MemorySection } from "./memory";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/design",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, string>) =>
      Object.entries(values ?? {}).reduce(
        (text, [name, value]) => text.replaceAll(`{{${name}}}`, value),
        key,
      ),
  }),
}));

function show(readOnly = false) {
  http.GET.mockResolvedValue({
    data: {
      items: [
        { id: "mem_book", key: "handbook", name: "Handbook" },
        { id: "mem_prefs", key: "user-prefs", name: "Preferences" },
      ],
      next_cursor: null,
    },
  });
  const drafts: AgentDraft[] = [];
  function Editor() {
    const draft = useAgentDraft({
      ...initialConfig(),
      memory_mounts: [
        { name: "handbook", memory_id: "mem_book", access: "read" },
      ],
    });
    drafts.push(draft);
    const submit = vi.fn();
    // The section's dialog must not submit the agent editor around it.
    return (
      <form onSubmit={submit} data-testid="editor">
        <MemorySection draft={draft} readOnly={readOnly} />
      </form>
    );
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <Editor />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), draft: () => drafts.at(-1)! };
}

it("adds, changes and removes the agent's default memories", async () => {
  const { user, draft } = show();
  expect(await screen.findByRole("link", { name: "Handbook" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Add memory" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("combobox", { name: "Memory" }));
  await user.click(
    await screen.findByRole("option", { name: "Preferences (user-prefs)" }),
  );
  await user.click(within(dialog).getByRole("button", { name: "Add memory" }));
  expect(await screen.findByRole("link", { name: "Preferences" })).toBeTruthy();
  await user.click(
    screen.getByRole("combobox", { name: "Access for handbook" }),
  );
  await user.click(await screen.findByRole("option", { name: "Write" }));
  await user.click(screen.getByRole("button", { name: "Remove user-prefs" }));
  expect(draft().memoryMounts).toEqual([
    { name: "handbook", memory_id: "mem_book", access: "write" },
  ]);
  expect(draft().dirty).toBe(true);
});

it("lists default memories read-only", async () => {
  show(true);
  expect(await screen.findByRole("link", { name: "Handbook" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add memory" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Remove handbook" })).toBeNull();
  expect(screen.getByText("Read")).toBeTruthy();
});
