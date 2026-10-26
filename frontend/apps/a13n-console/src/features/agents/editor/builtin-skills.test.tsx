import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { BuiltinSkillSelections } from "./builtin-skills";
import { buildDraftConfig, useAgentDraft } from "./draft";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../../auth/context", () => ({
  useClient: () => ({ workspace: () => http }),
}));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: { name?: string }) =>
      key.replace("{{name}}", values?.name ?? ""),
  }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

function Editor({
  selected = false,
  readOnly = false,
  save,
}: {
  selected?: boolean;
  readOnly?: boolean;
  save: (value: unknown) => void;
}) {
  const draft = useAgentDraft({
    model: "test-model",
    builtin_skills: selected ? ["platform-operations"] : [],
  });
  return (
    <>
      <BuiltinSkillSelections
        draft={draft}
        readOnly={readOnly}
        hostSkills={readOnly}
      />
      <button
        onClick={() =>
          save(buildDraftConfig(draft, undefined, (value) => value))
        }
      >
        Save
      </button>
    </>
  );
}

function open(options: { selected?: boolean; readOnly?: boolean } = {}) {
  const save = vi.fn();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={cache}>
      <Editor {...options} save={save} />
    </QueryClientProvider>,
  );
  return { save, user: userEvent.setup() };
}

it("selects a builtin from the server catalog and saves its name", async () => {
  http.GET.mockResolvedValue({
    data: {
      items: [
        { name: "platform-operations", description: "Manage schedules." },
      ],
    },
    response: new Response(),
  });
  const { save, user } = open();
  const selection = await screen.findByRole("checkbox", {
    name: "Enable platform-operations",
  });
  await user.click(selection);
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(save).toHaveBeenLastCalledWith(
    expect.objectContaining({
      ok: true,
      config: expect.objectContaining({
        builtin_skills: ["platform-operations"],
      }),
    }),
  );
  await user.click(selection);
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(save).toHaveBeenLastCalledWith(
    expect.objectContaining({
      ok: true,
      config: expect.objectContaining({ builtin_skills: [] }),
    }),
  );
});

it("keeps saved selections on catalog failure and allows explicit removal", async () => {
  http.GET.mockRejectedValue(new Error("Catalog unavailable"));
  const { save, user } = open({ selected: true });
  await screen.findByText("Catalog unavailable");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(save).toHaveBeenLastCalledWith(
    expect.objectContaining({
      config: expect.objectContaining({
        builtin_skills: ["platform-operations"],
      }),
    }),
  );
  await user.click(
    screen.getByRole("checkbox", { name: "Enable platform-operations" }),
  );
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(save).toHaveBeenLastCalledWith(
    expect.objectContaining({
      config: expect.objectContaining({ builtin_skills: [] }),
    }),
  );
});

it("shows builtin selections as read-only for protected configurations", async () => {
  http.GET.mockResolvedValue({
    data: {
      items: [
        { name: "platform-operations", description: "Manage schedules." },
      ],
    },
    response: new Response(),
  });
  const { save, user } = open({ selected: true, readOnly: true });
  const selection = await screen.findByRole("checkbox", {
    name: "Enable platform-operations",
  });
  expect(selection.getAttribute("aria-checked")).toBe("true");
  await user.click(selection);
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(save).toHaveBeenLastCalledWith(
    expect.objectContaining({
      config: expect.objectContaining({
        builtin_skills: ["platform-operations"],
      }),
    }),
  );
});
