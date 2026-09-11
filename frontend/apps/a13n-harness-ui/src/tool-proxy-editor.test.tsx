// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { parse } from "yaml";
import { ToolProxyEditor } from "./tool-proxy-editor";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function fixture(fail = false, suffix = "") {
  let content =
    '# Keep this comment\nschema_version: "1"\nkind: agent\nid: agent-main\nname: Main\ninstructions: Keep exact instructions.\n' +
    suffix;
  const writes: string[] = [];
  const fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    const json = (value: unknown, status = 200) =>
      new Response(JSON.stringify(value), { status });
    if (path === "/api/configuration/sources")
      return json({
        sources: [
          {
            relative_path: "agents/main.yaml",
            resource_kind: "agent",
            resource_ids: ["agent-main"],
            writable: true,
          },
        ],
      });
    if (init?.method === "POST")
      return fail
        ? json({ error: { message: "Selected source is unavailable." } }, 400)
        : json({ candidate_digest: "candidate" });
    if (init?.method === "PUT") {
      content = JSON.parse(String(init.body)).content;
      writes.push(content);
      return json({ action: "updated" });
    }
    return json({
      content,
      writable: true,
      agent_tool_proxy: {
        agent_id: "agent-main",
        tool_proxy: parse(content).tool_proxy ?? {
          groups: {},
          config: {
            search_name: "search_proxy_tools",
            call_name: "call_proxy_tool",
            max_results: 10,
            max_search_bytes: 32768,
          },
        },
        sources: [
          {
            resource_id: "mcp-docs",
            name: "Docs",
            kind: "mcp_server",
            enabled: true,
          },
          {
            resource_id: "plugin-memory",
            name: "Memory",
            kind: "harness_plugin",
            enabled: false,
          },
        ],
      },
    });
  });
  vi.stubGlobal("fetch", fetcher);
  return { writes, fetcher };
}

async function addGroup() {
  await screen.findByText("Keep direct tools, group the rest");
  fireEvent.click(screen.getByRole("button", { name: "Add group" }));
  fireEvent.change(screen.getByLabelText("Group name"), {
    target: { value: "knowledge" },
  });
  fireEvent.change(screen.getByLabelText("Description"), {
    target: { value: "Search knowledge." },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: /Docs/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /Memory/ }));
}

it("creates, renames and removes groups through validated Agent source writes and readback", async () => {
  const { writes } = fixture();
  render(<ToolProxyEditor apiKey="" />);
  await addGroup();
  expect(
    screen.getByLabelText("Source presentation summary").textContent,
  ).toContain("1 dormant");
  fireEvent.click(screen.getByRole("button", { name: "Save groups" }));
  await screen.findByText(/Saved and read back/);
  expect(writes).toHaveLength(1);
  expect(writes[0]).toContain("# Keep this comment");
  expect(parse(writes[0]).instructions).toBe("Keep exact instructions.");
  expect(parse(writes[0]).tool_proxy.groups.knowledge).toEqual({
    description: "Search knowledge.",
    mcp_servers: ["mcp-docs"],
    harness_plugins: ["plugin-memory"],
  });
  fireEvent.change(screen.getByLabelText("Group name"), {
    target: { value: "research" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save groups" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  await screen.findByText(/Saved and read back/);
  expect(Object.keys(parse(writes[1]).tool_proxy.groups)).toEqual(["research"]);
  fireEvent.click(screen.getByRole("button", { name: "Remove group" }));
  fireEvent.click(screen.getByRole("button", { name: "Save groups" }));
  await waitFor(() => expect(writes).toHaveLength(3));
  expect(parse(writes[2]).tool_proxy.groups).toEqual({});
});

it("retains failed drafts and never writes a rejected candidate", async () => {
  const { writes } = fixture(true);
  render(<ToolProxyEditor apiKey="" />);
  await addGroup();
  fireEvent.click(screen.getByRole("button", { name: "Save groups" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("alert").textContent).toBe(
    "Selected source is unavailable.",
  );
  expect(writes).toEqual([]);
  expect((screen.getByLabelText("Group name") as HTMLInputElement).value).toBe(
    "knowledge",
  );
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  await screen.findByText("Keep direct tools, group the rest");
});

it("prevents a source from joining two groups", async () => {
  fixture();
  render(<ToolProxyEditor apiKey="" />);
  await addGroup();
  fireEvent.click(screen.getByRole("button", { name: "Add group" }));
  const group = screen.getByRole("region", { name: "Group new" });
  expect(
    (within(group).getByRole("checkbox", { name: /Docs/ }) as HTMLInputElement)
      .disabled,
  ).toBe(true);
});

it.each(["tool_proxy: null\n", "tool_proxy:\n"])(
  "edits an accepted null proxy field: %s",
  async (suffix) => {
    const { writes } = fixture(false, suffix);
    render(<ToolProxyEditor apiKey="" />);
    await addGroup();
    fireEvent.click(screen.getByRole("button", { name: "Save groups" }));
    await screen.findByText(/Saved and read back/);
    expect(writes).toHaveLength(1);
    expect(writes[0]).toContain("# Keep this comment");
    expect(parse(writes[0]).tool_proxy.groups.knowledge.mcp_servers).toEqual([
      "mcp-docs",
    ]);
  },
);
