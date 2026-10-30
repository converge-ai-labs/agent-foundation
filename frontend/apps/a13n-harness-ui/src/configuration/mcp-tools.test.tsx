// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { McpTools } from "./mcp-tools";
import { DraftContext } from "./drafts";

const api = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn() }));
vi.mock("../transport/context", () => ({
  useTransport: () => ({ client: api }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
const tools = [
  { name: "search", description: "Search the web", input_schema: {} },
  { name: "fetch_content", description: "Read a web page", input_schema: {} },
  { name: "transfer", description: "Transfer funds", input_schema: {} },
];
function setup(drafts = new Map(), selected: string[] | null = ["search"]) {
  api.GET.mockResolvedValue({
    data: {
      server_id: "mcp-one",
      name: "One",
      resource_digest: "a".repeat(64),
      tools: selected,
    },
  });
  api.POST.mockResolvedValue({ data: tools });
  api.PUT.mockResolvedValue({ data: {} });
  return render(
    <DraftContext.Provider value={drafts}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <McpTools serverId="mcp-one" />
      </QueryClientProvider>
    </DraftContext.Provider>,
  );
}
it("loads tools explicitly and saves selected names while preserving filtered-out selections", async () => {
  setup();
  fireEvent.click(await screen.findByRole("button", { name: "Load tools" }));
  fireEvent.click(
    await screen.findByRole("checkbox", { name: "Enable fetch_content" }),
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Search tools" }), {
    target: { value: "fetch" },
  });
  expect(screen.queryByRole("checkbox", { name: "Enable search" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(api.PUT).toHaveBeenCalledWith(
      "/api/mcp/{server_id}/tools",
      expect.objectContaining({
        body: {
          resource_digest: "a".repeat(64),
          tools: ["search", "fetch_content"],
        },
      }),
    ),
  );
});
it("retains the list and unsaved selection when refresh fails", async () => {
  setup();
  fireEvent.click(await screen.findByRole("button", { name: "Load tools" }));
  fireEvent.click(
    await screen.findByRole("checkbox", { name: "Enable fetch_content" }),
  );
  api.POST.mockRejectedValueOnce(new Error("Could not load tools"));
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByText("Could not load tools");
  expect(
    screen
      .getByRole("checkbox", { name: "Enable fetch_content" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  expect(
    screen.getByRole("checkbox", { name: "Enable transfer" }),
  ).toBeTruthy();
});

it("restores unsaved selections after leaving and reopening the editor", async () => {
  const drafts = new Map();
  const first = setup(drafts);
  fireEvent.click(await screen.findByRole("button", { name: "Load tools" }));
  fireEvent.click(
    await screen.findByRole("checkbox", { name: "Enable fetch_content" }),
  );
  first.unmount();
  setup(drafts);
  expect(
    (
      await screen.findByRole("checkbox", { name: "Enable fetch_content" })
    ).getAttribute("aria-checked"),
  ).toBe("true");
  expect(
    screen.getByRole("button", { name: "Save" }).hasAttribute("disabled"),
  ).toBe(false);
});

it("discovers before switching from all tools to a restricted selection", async () => {
  setup(new Map(), null);
  let finish!: (value: { data: typeof tools }) => void;
  api.POST.mockReturnValueOnce(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("combobox", { name: "Available tools" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Selected tools" }),
  );
  await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("button", { name: "Save" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(api.PUT).not.toHaveBeenCalled();
  finish({ data: tools });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Save" })).toHaveProperty(
      "disabled",
      false,
    ),
  );
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(api.PUT).toHaveBeenCalledWith(
      "/api/mcp/{server_id}/tools",
      expect.objectContaining({
        body: {
          resource_digest: "a".repeat(64),
          tools: tools.map((tool) => tool.name),
        },
      }),
    ),
  );
});

it("keeps all tools enabled when discovery for selected mode fails", async () => {
  setup(new Map(), null);
  api.POST.mockRejectedValueOnce(new Error("Could not load tools"));
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("combobox", { name: "Available tools" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Selected tools" }),
  );
  await screen.findByText("Could not load tools");
  expect(
    screen.getByRole("combobox", { name: "Available tools" }).textContent,
  ).toContain("All tools");
  expect(screen.getByRole("button", { name: "Save" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(api.PUT).not.toHaveBeenCalled();
});
