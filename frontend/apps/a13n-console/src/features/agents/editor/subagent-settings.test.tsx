import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../../shared/api";
import { SubagentSettings } from "./subagent-settings";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../../auth/context", () => ({
  useClient: () => ({ workspace: () => http }),
}));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
const child = {
  id: "ap_child",
  name: "Researcher",
  description: "Research sources",
  image_url: null,
  default_revision_id: "ar_pinned",
  archived_at: null,
} as Schema["Agent"];
function setup(props: Partial<Parameters<typeof SubagentSettings>[0]> = {}) {
  http.GET.mockResolvedValue({
    data: {
      items: [{ id: "ar_pinned", number: 2, note: "Reviewed" }],
      next_cursor: null,
    },
    response: new Response(),
  });
  const onApply = vi.fn(),
    onClose = vi.fn();
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <SubagentSettings
        usedNames={[]}
        mode="async"
        agents={[child]}
        loading={false}
        refresh={vi.fn()}
        readOnly={false}
        control={{ controlledOpen: true, onClose }}
        onApply={onApply}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onApply, onClose, user: userEvent.setup() };
}
it("pins the selected default version and stages context edits without saving the parent", async () => {
  const { user, onApply, onClose } = setup();
  expect(
    screen.getByRole("link", { name: /Create agent/ }).getAttribute("target"),
  ).toBe("_blank");
  await user.click(screen.getByRole("button", { name: /Researcher/ }));
  await user.click(screen.getByRole("button", { name: "Advanced settings" }));
  const historyHelp = screen.getByRole("button", {
    name: "About conversation history",
  });
  for (let i = 0; i < 6 && document.activeElement !== historyHelp; i++)
    await user.tab();
  expect(document.activeElement).toBe(historyHelp);
  expect(await screen.findByText(/no new summary is generated/)).toBeTruthy();
  await user.click(
    screen.getByRole("combobox", { name: "Conversation history" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Existing summary" }),
  );
  await user.click(screen.getByRole("button", { name: "Add to draft" }));
  expect(onApply).toHaveBeenCalledWith("researcher", {
    agent_id: child.id,
    revision_id: "ar_pinned",
    context: { history: "summary" },
  });
  expect(onClose).toHaveBeenCalledOnce();
});
it("retains unexposed policy fields, rejects duplicate names, and discards canceled edits", async () => {
  const initial = {
    agent_id: child.id,
    revision_id: "ar_pinned",
    context: { task_state: "isolated" as const },
    environment: { mode: "dedicated" as const, template_id: "et_saved" },
  };
  const { user, onApply, onClose } = setup({
    initialName: "researcher",
    initial,
    usedNames: ["researcher", "taken"],
    mode: "inline",
  });
  await user.clear(screen.getByLabelText("Calling name"));
  await user.type(screen.getByLabelText("Calling name"), "taken");
  await user.click(screen.getByRole("button", { name: "Apply to draft" }));
  expect(screen.getByText("This calling name is already in use.")).toBeTruthy();
  expect(onApply).not.toHaveBeenCalled();
  await user.clear(screen.getByLabelText("Calling name"));
  await user.type(screen.getByLabelText("Calling name"), "renamed");
  await user.click(screen.getByRole("button", { name: "Apply to draft" }));
  expect(onApply).toHaveBeenCalledWith("renamed", initial);
  onApply.mockClear();
  onClose.mockClear();
  await user.type(
    screen.getByLabelText("Delegation description (optional)"),
    "Draft only",
  );
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(onApply).not.toHaveBeenCalled();
  expect(onClose).toHaveBeenCalledOnce();
});
it("keeps unavailable saved references inspectable in read-only configurations", async () => {
  setup({
    initialName: "lost",
    initial: {
      agent_id: "ap_missing",
      revision_id: "ar_missing",
      description: "Keep me",
    },
    agents: [],
    readOnly: true,
  });
  expect(screen.getByText("ar_missing")).toBeTruthy();
  expect(screen.getByText("Keep me")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Apply to draft" })).toBeNull();
});
