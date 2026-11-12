// @vitest-environment jsdom
import { useState } from "react";
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
import { MemoryRouter } from "react-router";
import { SchemaForm } from "./form";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { i18n } from "../i18n";
import { ApiError, type Schema } from "../transport/client";
import { CreateConnector } from "./create";
import { ConnectorSetup } from "./setup";
import { ConnectorOffer } from "./offers";
import { ConnectorsSection } from "./connections";
import { clearSecrets, defaultValue, type FormSchema } from "./schema";

const transport = vi.hoisted(() => ({
  client: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() },
  shareId: undefined as string | undefined,
}));
vi.mock("../transport/context", () => ({ useTransport: () => transport }));
let cache: QueryClient;
const form: FormSchema = {
  type: "object",
  properties: {
    token: {
      type: "string",
      title: "GitHub personal access token",
      writeOnly: true,
    },
    label: { type: "string", title: "Name" },
  },
};
const snapshot: Schema<"SetupSnapshot"> = {
  session_id: "setup-one",
  connector_id: "connector-one",
  version: 1,
  expires_at: "2099-01-01T00:00:00Z",
  step: {
    key: "configure",
    title: "Configure GitHub",
    state: "input",
    form,
    values: { label: "Draft" },
    actions: [{ id: "save", label: "Save connection", kind: "submit" }],
  },
};
const instance: Schema<"Instance"> = {
  connector_id: "connector-one",
  kind: "github",
  name: "Work account",
  version: 1,
  enabled: true,
  deliver_to_bot: false,
  settings: {},
  status: {
    state: "ready",
    summary: "Configured. Upstream access is checked when connecting.",
  },
};
const catalog: Schema<"CatalogItem">[] = [
  {
    key: "github",
    available: true,
    declaration: {
      key: "github",
      name: "GitHub",
      description: "GitHub",
      configuration_schema: { type: "object" },
    },
  },
];
function mount(children: React.ReactNode) {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={cache}>{children}</QueryClientProvider>
    </MemoryRouter>,
  );
}
beforeEach(() => {
  transport.shareId = undefined;
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  transport.client.POST.mockResolvedValue({ data: snapshot });
  transport.client.GET.mockImplementation(async (url: string) => ({
    data: url.endsWith("catalog")
      ? catalog
      : url === "/api/connectors"
        ? [instance]
        : snapshot,
  }));
});
afterEach(async () => {
  cleanup();
  cache.clear();
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
  localStorage.clear();
  sessionStorage.clear();
});

it("renders an unregistered service logo from its backend declaration", async () => {
  mount(
    <CreateConnector
      item={{
        key: "custom-service",
        available: true,
        declaration: {
          key: "custom-service",
          name: "Custom service",
          description: "A third-party service",
          logo_url: "https://example.invalid/custom-logo.png",
          configuration_schema: { type: "object" },
        },
      }}
      onClose={() => {}}
    />,
  );
  expect(
    screen.getByRole("heading", { name: "Add Custom service" }),
  ).toBeTruthy();
  const logo = document.querySelector<HTMLImageElement>("img");
  expect(logo?.src).toBe("https://example.invalid/custom-logo.png");
  expect(logo?.getAttribute("referrerpolicy")).toBe("no-referrer");
  fireEvent.error(logo!);
  expect(document.querySelector("img")).toBeNull();
  expect(
    screen.getByRole("heading", { name: "Add Custom service" }),
  ).toBeTruthy();
  expect(transport.client.POST).not.toHaveBeenCalled();
});

it("clears private fields on dispatch, preserves other drafts and reconciles unknown outcomes without replay", async () => {
  mount(<ConnectorSetup connectorId="connector-one" onClose={() => {}} />);
  const token = await screen.findByLabelText("GitHub personal access token");
  fireEvent.change(token, { target: { value: "private-test-value" } });
  fireEvent.change(screen.getByLabelText("Name"), {
    target: { value: "Unsaved label" },
  });
  await act(async () => {
    await i18n.changeLanguage("zh-CN");
  });
  expect(
    (screen.getByLabelText("GitHub 个人访问令牌") as HTMLInputElement).value,
  ).toBe("private-test-value");
  expect(transport.client.POST).toHaveBeenCalledTimes(1);
  transport.client.POST.mockRejectedValueOnce(new TypeError("Offline"));
  await userEvent.click(screen.getByRole("button", { name: "保存连接" }));
  await screen.findByText(/操作结果未知/);
  expect(
    (screen.getByLabelText("GitHub 个人访问令牌") as HTMLInputElement).value,
  ).toBe("");
  expect((screen.getByLabelText("名称") as HTMLInputElement).value).toBe(
    "Unsaved label",
  );
  const command = transport.client.POST.mock.calls[1][1].body;
  expect(command.values.token).toBe("private-test-value");
  expect(JSON.stringify(cache.getQueryCache().getAll())).not.toContain(
    "private-test-value",
  );
  expect(cache.getMutationCache().getAll()).toHaveLength(0);
  expect(
    JSON.stringify(localStorage) + JSON.stringify(sessionStorage),
  ).not.toContain("private-test-value");
  await userEvent.click(screen.getByRole("button", { name: "刷新状态" }));
  await waitFor(() => expect(screen.queryByText(/操作结果未知/)).toBeNull());
  expect(transport.client.POST).toHaveBeenCalledTimes(2);
  expect(
    screen.getByRole("button", { name: "保存连接" }).hasAttribute("disabled"),
  ).toBe(false);
});

it("preserves settings drafts when another tab or setup changes the saved version", async () => {
  mount(<ConnectorsSection />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Actions for Work account" }),
  );
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "Manage" }),
  );
  fireEvent.change(screen.getByLabelText("Connection name"), {
    target: { value: "My draft" },
  });
  act(() =>
    cache.setQueryData(
      ["connectors", "instances"],
      [{ ...instance, version: 2, name: "Other tab" }],
    ),
  );
  expect(
    (screen.getByLabelText("Connection name") as HTMLInputElement).value,
  ).toBe("My draft");
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Save settings" })
        .hasAttribute("disabled"),
    ).toBe(true),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Reload saved settings" }),
  );
  expect(
    (screen.getByLabelText("Connection name") as HTMLInputElement).value,
  ).toBe("Other tab");
});

it("renders published guest offers without owner queries or active setup controls", async () => {
  transport.shareId = "share-one";
  mount(
    <ConnectorOffer
      offer={{
        offer_id: "offer-one",
        thread_id: "thread-one",
        connector_key: "github",
        created_at: "2026-11-11T00:00:00Z",
        text: "Connect GitHub",
      }}
    />,
  );
  expect(
    screen.getByText("Only the instance owner can configure this connection."),
  ).toBeTruthy();
  expect(
    within(
      screen.getByRole("region", { name: "Connector setup offer" }),
    ).queryByRole("button"),
  ).toBeNull();
  expect(transport.client.GET).not.toHaveBeenCalled();
});

it("projects nested write-only fields and nullable repeatable rows through the production schema logic", () => {
  const schema: FormSchema = {
    type: "object",
    $defs: {
      Header: {
        type: "object",
        properties: {
          name: { type: "string" },
          secret: { type: "string", writeOnly: true },
        },
      },
    },
    properties: {
      headers: { type: "array", items: { $ref: "#/$defs/Header" } },
      tools: {
        anyOf: [{ type: "array", items: { type: "string" } }, { type: "null" }],
        default: null,
      },
    },
  };
  expect(defaultValue(schema)).toEqual({ headers: [], tools: null });
  expect(
    clearSecrets(schema, {
      headers: [{ name: "Authorization", secret: "private-value" }],
      tools: [],
    }),
  ).toEqual({ headers: [{ name: "Authorization", secret: "" }], tools: [] });
});

it("cancels an in-flight setup action and ignores its late response", async () => {
  mount(<ConnectorSetup connectorId="connector-one" onClose={() => {}} />);
  await screen.findByLabelText("GitHub personal access token");
  let finish: (value: { data: Schema<"SetupSnapshot"> }) => void = () => {};
  transport.client.POST.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Save connection" }),
  );
  expect(
    screen
      .getByRole("button", { name: "Save connection" })
      .hasAttribute("disabled"),
  ).toBe(true);
  transport.client.POST.mockResolvedValueOnce({
    data: {
      ...snapshot,
      version: 3,
      step: { key: "cancelled", title: "Setup cancelled", state: "cancelled" },
    },
  });
  await userEvent.click(screen.getByRole("button", { name: "Cancel setup" }));
  await screen.findByText("Setup cancelled");
  expect(transport.client.POST.mock.calls[2][1].body.action).toBe("cancel");
  await act(async () => {
    finish({ data: { ...snapshot, version: 2 } });
  });
  expect(screen.getByText("Setup cancelled")).toBeTruthy();
  expect(screen.queryByLabelText("GitHub personal access token")).toBeNull();
});

it("confirms a dirty cancellation and sends the explicit cancel action", async () => {
  const onClose = vi.fn();
  mount(<ConnectorSetup connectorId="connector-one" onClose={onClose} />);
  fireEvent.change(
    await screen.findByLabelText("GitHub personal access token"),
    { target: { value: "fictional-private-value" } },
  );
  await userEvent.click(screen.getByRole("button", { name: "Cancel setup" }));
  expect(transport.client.POST).toHaveBeenCalledTimes(1);
  transport.client.POST.mockResolvedValueOnce({
    data: {
      ...snapshot,
      version: 2,
      step: { key: "cancelled", title: "Setup cancelled", state: "cancelled" },
    },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Discard changes" }),
  );
  await screen.findByText("Setup cancelled");
  expect(transport.client.POST.mock.calls[1][1].body.action).toBe("cancel");
  expect(onClose).not.toHaveBeenCalled();
});

it("rebases clean account drafts without reporting a conflict against setup saves", async () => {
  mount(<ConnectorsSection />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Actions for Work account" }),
  );
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "Manage" }),
  );
  await screen.findByLabelText("Connection name");
  act(() =>
    cache.setQueryData(
      ["connectors", "instances"],
      [{ ...instance, version: 2, name: "Updated by setup" }],
    ),
  );
  await waitFor(() =>
    expect(
      (screen.getByLabelText("Connection name") as HTMLInputElement).value,
    ).toBe("Updated by setup"),
  );
  expect(screen.queryByText(/Saved settings changed/)).toBeNull();
  fireEvent.change(screen.getByLabelText("Connection name"), {
    target: { value: "My account" },
  });
  transport.client.PUT.mockResolvedValueOnce({
    data: { ...instance, name: "My account", version: 3 },
  });
  await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
  await waitFor(() => expect(transport.client.PUT).toHaveBeenCalledOnce());
  expect(transport.client.PUT.mock.calls[0][1].body.expected_version).toBe(2);
});

it("names new accounts before persistence and offers cleanup for incomplete setup", async () => {
  transport.client.GET.mockImplementation(async (url: string) => ({
    data: url.endsWith("catalog")
      ? catalog
      : url === "/api/connectors"
        ? [{ ...instance, name: "GitHub" }]
        : snapshot,
  }));
  mount(<ConnectorsSection />);
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Add connection" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  await userEvent.click(screen.getByRole("button", { name: "Add connection" }));
  await userEvent.click(
    await screen.findByRole("menuitem", { name: /GitHub/ }),
  );
  expect(
    (screen.getByLabelText("Connection name") as HTMLInputElement).value,
  ).toBe("GitHub 2");
  expect(transport.client.PUT).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(transport.client.PUT).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Add connection" }));
  await userEvent.click(
    await screen.findByRole("menuitem", { name: /GitHub/ }),
  );
  transport.client.PUT.mockResolvedValueOnce({
    data: {
      ...instance,
      connector_id: "connector-new",
      name: "GitHub 2",
      status: {
        state: "unconfigured",
        summary: "Complete connection setup to use its tools.",
      },
    },
  });
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByLabelText("GitHub personal access token");
  await userEvent.click(
    screen.getByRole("button", { name: "Close connection setup" }),
  );
  await screen.findByText("Keep this connection for later?");
  expect(screen.getByRole("button", { name: "Keep for later" })).toBeTruthy();
  transport.client.DELETE.mockResolvedValueOnce({ data: {} });
  await userEvent.click(
    screen.getByRole("button", { name: "Remove connection" }),
  );
  await waitFor(() => expect(transport.client.DELETE).toHaveBeenCalledOnce());
});

it("keeps saved connection actions reachable in the dialog and supports implicit primary submission", async () => {
  const saved: Schema<"SetupSnapshot"> = {
    ...snapshot,
    version: 2,
    step: {
      key: "saved",
      title: "Connection saved",
      state: "input",
      actions: [
        {
          id: "opaque-probe",
          label: "Test connection",
          kind: "submit",
          icon: "test",
        },
        { id: "done", label: "Done", kind: "submit", icon: "done" },
      ],
    },
  };
  mount(
    <ConnectorSetup
      connectorId="connector-one"
      name="Work account"
      onClose={() => {}}
    />,
  );
  const token = await screen.findByLabelText("GitHub personal access token");
  expect(screen.queryByRole("button", { name: "Test connection" })).toBeNull();
  fireEvent.change(token, { target: { value: "fictional-private-value" } });
  transport.client.POST.mockResolvedValueOnce({ data: saved });
  fireEvent.submit(token.closest("form")!);
  await screen.findByText("Connection saved");
  expect(transport.client.POST.mock.calls[1][1].body.action).toBe("save");
  const testButton = screen.getByRole("button", { name: "Test connection" });
  expect(testButton.textContent).toBe("");
  expect(screen.getByRole("button", { name: "Done" }).textContent).toBe("Done");
  transport.client.POST.mockResolvedValueOnce({
    data: {
      ...saved,
      version: 3,
      step: {
        ...saved.step,
        error:
          "MCP connection test failed. Check endpoint, credentials and upstream permissions.",
      },
    },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Test connection" }),
  );
  await screen.findByRole("alert");
  expect(transport.client.POST.mock.calls[2][1].body.action).toBe(
    "opaque-probe",
  );
  expect(screen.getByRole("button", { name: "Done" })).toBeTruthy();
  expect(screen.queryByLabelText("GitHub personal access token")).toBeNull();
});

it("shows saved secret hints and explicit nullable tool selection without a native select", async () => {
  const toolForm: FormSchema = {
    type: "object",
    "x-a13n-saved": ["/token"],
    properties: {
      token: form.properties!.token,
      tools: {
        title: "Tools",
        default: null,
        "x-a13n-null-label": "All tools",
        "x-a13n-selected-label": "Only selected tools",
        "x-a13n-empty-label": "No tools will be available",
        anyOf: [{ type: "array", items: { type: "string" } }, { type: "null" }],
      },
    },
  };
  function Editor() {
    const [value, setValue] = useState({
      token: "",
      tools: null,
    } as import("./schema").Json);
    return <SchemaForm schema={toolForm} value={value} onChange={setValue} />;
  }
  mount(<Editor />);
  expect(screen.getByText("Saved")).toBeTruthy();
  expect(screen.getByText("All tools")).toBeTruthy();
  await userEvent.click(screen.getByRole("combobox", { name: "Tools" }));
  await userEvent.click(
    await screen.findByRole("option", { name: "Only selected tools" }),
  );
  await screen.findByText("No tools will be available");
  expect(document.querySelector("select")).toBeNull();
});

it("resolves chat offers from saved readiness without opening a form inline or starting work", async () => {
  transport.client.GET.mockImplementation(async (url: string) => ({
    data: url.endsWith("catalog")
      ? catalog
      : [
          {
            ...instance,
            status: {
              state: "unconfigured",
              summary: "Complete connection setup to use its tools.",
            },
          },
        ],
  }));
  mount(
    <ConnectorOffer
      offer={{
        offer_id: "offer-one",
        thread_id: "thread-one",
        connector_key: "github",
        connector_id: instance.connector_id,
        created_at: "2026-11-11T00:00:00Z",
      }}
    />,
  );
  const region = screen.getByRole("region", { name: "Connector setup offer" });
  await userEvent.click(
    await screen.findByRole("button", { name: "Continue setup" }),
  );
  await screen.findByRole("dialog");
  expect(
    within(region).queryByLabelText("GitHub personal access token"),
  ).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Close connection setup" }),
  );
  act(() => cache.setQueryData(["connectors", "instances"], [instance]));
  await screen.findByText("Work account is connected");
  expect(
    screen.getByRole("link", { name: "Manage" }).getAttribute("href"),
  ).toBe("/settings/connections");
  expect(screen.queryByRole("button", { name: "Continue setup" })).toBeNull();
  expect(transport.client.POST).toHaveBeenCalledTimes(1);
});

it("reconciles a lost create response using the same account ID without overwriting saved settings", async () => {
  const accounts = new Map<string, Schema<"Instance">>();
  transport.client.PUT.mockImplementation(async (_url, options) => {
    const id = options.params.path.connector_id;
    if (accounts.has(id))
      throw new ApiError("Changed", 409, "connector_conflict");
    accounts.set(id, {
      ...instance,
      connector_id: id,
      name: options.body.name,
    });
    throw new TypeError("Response lost after commit");
  });
  transport.client.GET.mockImplementation(async (url, options) => ({
    data:
      url === "/api/connectors/{connector_id}"
        ? accounts.get(options.params.path.connector_id)
        : [...accounts.values()],
  }));
  mount(<CreateConnector item={catalog[0]} onClose={() => {}} />);
  fireEvent.change(screen.getByLabelText("Connection name"), {
    target: { value: "Original account" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("alert");
  fireEvent.change(screen.getByLabelText("Connection name"), {
    target: { value: "Changed draft" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByLabelText("GitHub personal access token");
  expect(accounts.size).toBe(1);
  const calls = transport.client.PUT.mock.calls;
  expect(calls).toHaveLength(2);
  expect(calls[0][1].params.path).toEqual(calls[1][1].params.path);
  expect(
    calls.every((call) => call[1].body.expected_version === undefined),
  ).toBe(true);
  expect(screen.getByText("Original account")).toBeTruthy();
  expect(transport.client.POST.mock.calls[0][1].params.path.connector_id).toBe(
    [...accounts.keys()][0],
  );
});
