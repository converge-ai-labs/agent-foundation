// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTransport } from "./transport/client";
import { TransportContext } from "./transport/context";
import { SharePanel } from "./sharing";
import { i18n } from "./i18n";

const disposals: (() => void)[] = [];
beforeEach(async () => {
  await i18n.changeLanguage("en");
});
afterEach(async () => {
  cleanup();
  disposals.splice(0).forEach((dispose) => dispose());
  vi.unstubAllGlobals();
  await i18n.changeLanguage("en");
});

function renderSharing({
  failCreate = false,
  failList = false,
  holdMethod,
  projectName = "Design studio",
}: {
  failCreate?: boolean;
  failList?: boolean;
  holdMethod?: "POST" | "PATCH" | "DELETE";
  projectName?: string;
} = {}) {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  Object.defineProperty(Element.prototype, "getAnimations", {
    configurable: true,
    value: () => [],
  });
  const user = userEvent.setup();
  const clipboard = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: clipboard },
  });
  const writes: { method: string; body?: unknown }[] = [];
  let grant = {
    share_id: "share-test",
    scope: "thread",
    target_id: "thread-one",
    permission: "view",
    created_at: "2026-10-19T12:00:00Z",
    expires_at: "2026-10-20T12:00:00Z",
  };
  let active = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const json = (value: unknown) =>
        new Response(JSON.stringify(value), {
          headers: { "Content-Type": "application/json" },
        });
      if (request.method !== "GET")
        writes.push({
          method: request.method,
          body:
            request.method === "DELETE"
              ? undefined
              : await request.clone().json(),
        });
      if (request.method === holdMethod) await pending;
      if (path === "/api/setup") return json({});
      if (path === "/api/projects")
        return json([{ project_id: "project-one", name: projectName }]);
      if (request.method === "POST") {
        if (failCreate) throw new TypeError("Connection interrupted");
        const body = await request.json();
        grant = {
          ...grant,
          scope: body.scope,
          target_id: body.target_id,
          permission: body.permission,
        };
        active = true;
        return json({ grant, key: "test-link-key" });
      }
      if (request.method === "PATCH") {
        grant = { ...grant, permission: (await request.json()).permission };
        return json(grant);
      }
      if (request.method === "DELETE") {
        active = false;
        return new Response(null, { status: 204 });
      }
      if (path === "/api/shares") {
        if (failList)
          return new Response(JSON.stringify({ error: { message: "Share" } }), {
            status: 400,
            headers: { "Content-Type": "application/json" },
          });
        return json(active ? [grant] : []);
      }
      throw new Error(path);
    }),
  );
  const transport = createTransport("test-owner", vi.fn());
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  disposals.push(() => {
    transport.close();
    queries.clear();
  });
  render(
    <QueryClientProvider client={queries}>
      <TransportContext value={transport}>
        <SharePanel
          open
          onClose={() => {}}
          threadId="thread-one"
          projectId="project-one"
        />
      </TransportContext>
    </QueryClientProvider>,
  );
  return { writes, clipboard, user, release };
}

it("creates only on request, copies the issued link, saves permission and revokes", async () => {
  const { writes, clipboard, user } = renderSharing();
  await screen.findByText("Manage links - 0");
  expect(writes).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Create link" }));
  const link = await screen.findByRole("textbox", { name: "Sharing link" });
  expect((link as HTMLInputElement).value).toContain(
    "/share/share-test#key=test-link-key",
  );
  expect(writes).toEqual([
    {
      method: "POST",
      body: {
        scope: "thread",
        target_id: "thread-one",
        permission: "view",
        lifetime_hours: 24,
      },
    },
  ]);
  expect(screen.queryByRole("combobox", { name: "Include" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Link access" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
  await screen.findByRole("button", { name: "Copied" });
  expect(clipboard).toHaveBeenCalledWith((link as HTMLInputElement).value);
  fireEvent.click(screen.getByText("Manage links - 1"));
  await user.click(screen.getByRole("combobox", { name: "Permission" }));
  await user.click(
    await screen.findByRole("option", { name: "Send messages" }),
  );
  expect(writes).toHaveLength(1);
  await screen.findByText(/Share only with people you trust/);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(
      within(screen.getByRole("region", { name: "Link sharing" })).getByText(
        "Send messages",
      ),
    ).toBeTruthy(),
  );
  expect(writes[1]).toEqual({
    method: "PATCH",
    body: { permission: "prompt" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
  await screen.findByText("Manage links - 0");
  expect(screen.queryByRole("textbox", { name: "Sharing link" })).toBeNull();
  expect(writes[2].method).toBe("DELETE");
  await user.click(screen.getByRole("button", { name: "Create link" }));
  await screen.findByRole("button", { name: "Copy link" });
  expect(screen.queryByRole("button", { name: "Copied" })).toBeNull();
  expect(writes).toHaveLength(4);
});

it("sets Project access and expiry, preserves the link on copy failure and resets for another link", async () => {
  const { writes, clipboard, user } = renderSharing();
  await screen.findByText("Manage links - 0");
  await user.click(screen.getByRole("combobox", { name: "Include" }));
  await user.click(
    await screen.findByRole("option", {
      name: "All chats in Design studio",
    }),
  );
  await user.click(screen.getByRole("combobox", { name: "Link access" }));
  await user.click(
    await screen.findByRole("option", { name: "Send messages" }),
  );
  expect(
    screen.getByText(
      /People can send messages using your agent’s tools and access/,
    ),
  ).toBeTruthy();
  expect(screen.getByText(/They can also start new chats/)).toBeTruthy();
  await user.click(screen.getByRole("combobox", { name: "Expires after" }));
  await user.click(await screen.findByRole("option", { name: "7 days" }));
  expect(writes).toEqual([]);
  await user.click(screen.getByRole("button", { name: "Create link" }));
  const link = await screen.findByRole("textbox", { name: "Sharing link" });
  expect(writes).toEqual([
    {
      method: "POST",
      body: {
        scope: "project",
        target_id: "project-one",
        permission: "prompt",
        lifetime_hours: 168,
      },
    },
  ]);
  clipboard.mockRejectedValueOnce(new Error("Clipboard unavailable"));
  await user.click(screen.getByRole("button", { name: "Copy link" }));
  await screen.findByText(/copy it manually/);
  expect((link as HTMLInputElement).value).toContain("#key=test-link-key");
  await user.click(screen.getByRole("button", { name: "Copy link" }));
  await screen.findByRole("button", { name: "Copied" });
  expect(writes).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "New link" }));
  expect(screen.queryByRole("textbox", { name: "Sharing link" })).toBeNull();
  expect(screen.queryByText(/copy it manually/)).toBeNull();
  expect(screen.getByRole("combobox", { name: "Include" })).toBeTruthy();
  expect(writes).toHaveLength(1);
});

it("keeps grant management available after an unknown creation outcome without retrying", async () => {
  const { writes, user } = renderSharing({ failCreate: true });
  await screen.findByText("Manage links - 0");
  await user.click(screen.getByRole("button", { name: "Create link" }));
  await screen.findByText(/The link may already exist/);
  expect(screen.getByText("Manage links - 0")).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: "Sharing link" })).toBeNull();
  expect(writes).toHaveLength(1);
  const calls = vi.mocked(fetch).mock.calls.length;
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(screen.getByRole("dialog", { name: "分享" })).toBeTruthy();
  expect(screen.getByText(/链接可能已创建/)).toBeTruthy();
  expect(
    screen.getByText("无法连接服务器。请检查网络连接后重试。"),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "管理链接 - 0" }));
  expect(screen.getByText("此聊天暂无分享链接。")).toBeTruthy();
  expect(vi.mocked(fetch).mock.calls).toHaveLength(calls);
  expect(writes).toHaveLength(1);
});

it("retains scope, permission, expiry and pending creation across language changes", async () => {
  const { writes, user, release } = renderSharing({
    holdMethod: "POST",
    projectName: "Share & <team>",
  });
  await screen.findByText("Manage links - 0");
  const scope = screen.getByRole("combobox", { name: "Include" });
  await user.click(scope);
  await user.click(
    await screen.findByRole("option", { name: "All chats in Share & <team>" }),
  );
  await user.click(screen.getByRole("combobox", { name: "Link access" }));
  await user.click(
    await screen.findByRole("option", { name: "Send messages" }),
  );
  await user.click(screen.getByRole("combobox", { name: "Expires after" }));
  await user.click(await screen.findByRole("option", { name: "7 days" }));
  const calls = vi.mocked(fetch).mock.calls.length;
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(screen.getByRole("combobox", { name: "分享范围" })).toBe(scope);
  expect(scope.textContent).toContain("Share & <team>中的所有聊天");
  expect(
    screen.getByRole("combobox", { name: "链接权限" }).textContent,
  ).toContain("发送消息");
  expect(
    screen.getByRole("combobox", { name: "有效期" }).textContent,
  ).toContain("7 天");
  expect(screen.getByText(/也可以新建聊天/)).toBeTruthy();
  expect(writes).toEqual([]);
  expect(vi.mocked(fetch).mock.calls).toHaveLength(calls);
  await user.click(screen.getByRole("button", { name: "创建链接" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  await act(() => i18n.changeLanguage("en"));
  expect(
    (screen.getByRole("button", { name: "Create link" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(writes).toEqual([
    {
      method: "POST",
      body: {
        scope: "project",
        target_id: "project-one",
        permission: "prompt",
        lifetime_hours: 168,
      },
    },
  ]);
  await act(async () => release());
  const link = await screen.findByRole("textbox", { name: "Sharing link" });
  const issued = (link as HTMLInputElement).value;
  const settledCalls = vi.mocked(fetch).mock.calls.length;
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(screen.getByRole("textbox", { name: "分享链接" })).toBe(link);
  expect((link as HTMLInputElement).value).toBe(issued);
  expect(
    screen.getByText(
      new Date("2026-10-20T12:00:00Z").toLocaleString("zh-CN", {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }),
    ),
  ).toBeTruthy();
  expect(writes).toHaveLength(1);
  expect(vi.mocked(fetch).mock.calls).toHaveLength(settledCalls);
});

it("retains a copy failure, issued key, selection and unsaved permission while translating", async () => {
  const { writes, clipboard, user } = renderSharing();
  await screen.findByText("Manage links - 0");
  await user.click(screen.getByRole("button", { name: "Create link" }));
  const link = (await screen.findByRole("textbox", {
    name: "Sharing link",
  })) as HTMLInputElement;
  await user.click(screen.getByRole("button", { name: "Manage links - 1" }));
  const permission = screen.getByRole("combobox", { name: "Permission" });
  await user.click(permission);
  await user.click(
    await screen.findByRole("option", { name: "Send messages" }),
  );
  clipboard.mockRejectedValueOnce(new Error("Clipboard unavailable"));
  await user.click(screen.getByRole("button", { name: "Copy link" }));
  await screen.findByText(/copy it manually/);
  fireEvent.focus(link);
  link.setSelectionRange(3, 12);
  const calls = vi.mocked(fetch).mock.calls.length;
  const issued = link.value;
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(screen.getByText("请选中上方链接并手动复制。")).toBeTruthy();
  expect(screen.getByRole("textbox", { name: "分享链接" })).toBe(link);
  expect(link.value).toBe(issued);
  expect([link.selectionStart, link.selectionEnd]).toEqual([3, 12]);
  expect(screen.getByRole("combobox", { name: "权限" })).toBe(permission);
  expect(permission.textContent).toContain("发送消息");
  expect(screen.getByRole("button", { name: "保存" })).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "管理链接 - 1" })
      .getAttribute("aria-expanded"),
  ).toBe("true");
  expect(screen.getByText(/创建于/)).toBeTruthy();
  expect(writes).toHaveLength(1);
  expect(vi.mocked(fetch).mock.calls).toHaveLength(calls);
  await user.click(screen.getByRole("button", { name: "复制链接" }));
  await screen.findByRole("button", { name: "已复制" });
  await act(() => i18n.changeLanguage("en"));
  expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy();
  expect(clipboard).toHaveBeenLastCalledWith(issued);
  expect(writes).toHaveLength(1);
  expect(vi.mocked(fetch).mock.calls).toHaveLength(calls);
});

it.each(["PATCH", "DELETE"] as const)(
  "does not replay a pending %s when the language changes",
  async (method) => {
    const { writes, user, release } = renderSharing({ holdMethod: method });
    await screen.findByText("Manage links - 0");
    await user.click(screen.getByRole("button", { name: "Create link" }));
    const link = await screen.findByRole("textbox", { name: "Sharing link" });
    await user.click(screen.getByRole("button", { name: "Manage links - 1" }));
    if (method === "PATCH") {
      await user.click(screen.getByRole("combobox", { name: "Permission" }));
      await user.click(
        await screen.findByRole("option", { name: "Send messages" }),
      );
    }
    await user.click(
      screen.getByRole("button", {
        name: method === "PATCH" ? "Save" : "Revoke",
      }),
    );
    await waitFor(() => expect(writes).toHaveLength(2));
    const calls = vi.mocked(fetch).mock.calls.length;
    await act(() => i18n.changeLanguage("zh-CN"));
    expect(
      (
        screen.getByRole("button", {
          name: method === "PATCH" ? "保存" : "撤销",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(screen.getByRole("textbox", { name: "分享链接" })).toBe(link);
    expect(writes).toHaveLength(2);
    expect(vi.mocked(fetch).mock.calls).toHaveLength(calls);
    await act(async () => release());
    if (method === "PATCH") {
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: "保存" })).toBeNull(),
      );
      expect(
        within(screen.getByRole("region", { name: "链接分享" })).getByText(
          "发送消息",
        ),
      ).toBeTruthy();
    } else {
      await screen.findByText("管理链接 - 0");
      expect(screen.queryByRole("textbox", { name: "分享链接" })).toBeNull();
    }
    expect(writes).toHaveLength(2);
  },
);

it("translates retry controls while preserving raw server diagnostics", async () => {
  const { writes, user } = renderSharing({ failList: true });
  await user.click(screen.getByRole("button", { name: "Manage links" }));
  await screen.findByRole("alert");
  const calls = vi.mocked(fetch).mock.calls.length;
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(within(screen.getByRole("alert")).getByText("Share")).toBeTruthy();
  expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  expect(vi.mocked(fetch).mock.calls).toHaveLength(calls);
  await user.click(screen.getByRole("button", { name: "重试" }));
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls).toHaveLength(calls + 1),
  );
  expect(writes).toEqual([]);
});
