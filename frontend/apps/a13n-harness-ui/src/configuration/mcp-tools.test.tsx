// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
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
function setup(drafts = new Map()) {
  api.GET.mockResolvedValue({
    data: {
      server_id: "mcp-one",
      name: "One",
      source_digest: "a".repeat(64),
      tools: ["search"],
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
          source_digest: "a".repeat(64),
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
