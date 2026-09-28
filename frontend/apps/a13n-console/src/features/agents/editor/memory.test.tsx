import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { expect, it, vi } from "vitest";
import { initialConfig } from "../configuration";
import { useAgentDraft, type AgentDraft } from "./draft";
import { MemorySection } from "./memory";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
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
        { id: "mem_book", name: "Handbook", kind: "file" },
        {
          id: "mem_prefs",

          name: "Preferences",
          kind: "file",
        },
        { id: "mem_facts", name: "Facts", kind: "record" },
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
        { name: "facts", memory_id: "mem_facts", access: "write" },
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
    await screen.findByRole("option", { name: "Preferences (mem_prefs)" }),
  );
  await user.click(within(dialog).getByRole("button", { name: "Add memory" }));
  expect(await screen.findByRole("link", { name: "Preferences" })).toBeTruthy();
  await user.click(
    screen.getByRole("combobox", { name: "Access for handbook" }),
  );
  await user.click(await screen.findByRole("option", { name: "Write" }));
  await user.click(screen.getByRole("button", { name: "Remove preferences" }));
  // A record memory recalls unless the agent turns it off.
  await user.click(screen.getByRole("switch", { name: "Recall for facts" }));
  expect(draft().memoryMounts).toEqual([
    { name: "handbook", memory_id: "mem_book", access: "write" },
    { name: "facts", memory_id: "mem_facts", access: "write", recall: false },
  ]);
  expect(draft().dirty).toBe(true);
});

it("lists default memories read-only", async () => {
  show(true);
  expect(await screen.findByRole("link", { name: "Handbook" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add memory" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Remove handbook" })).toBeNull();
  expect(screen.getByText("Read")).toBeTruthy();
  expect(screen.getByText("Recall")).toBeTruthy();
  expect(screen.queryByRole("switch")).toBeNull();
});
