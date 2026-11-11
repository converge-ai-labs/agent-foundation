// @vitest-environment jsdom
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
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { i18n } from "../i18n";
import type { Schema } from "../transport/client";
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
    <QueryClientProvider client={cache}>{children}</QueryClientProvider>,
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
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
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
  fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
  await waitFor(() => expect(screen.queryByText(/操作结果未知/)).toBeNull());
  expect(transport.client.POST).toHaveBeenCalledTimes(2);
  expect(
    screen.getByRole("button", { name: "保存连接" }).hasAttribute("disabled"),
  ).toBe(false);
});

it("preserves settings drafts when another tab or setup changes the saved version", async () => {
  mount(<ConnectorsSection />);
  fireEvent.click(await screen.findByRole("button", { name: "Manage" }));
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
  fireEvent.click(
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
  fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
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
  fireEvent.click(screen.getByRole("button", { name: "Cancel setup" }));
  await screen.findByText("Setup cancelled");
  expect(transport.client.POST.mock.calls[2][1].body.action).toBe("cancel");
  await act(async () => {
    finish({ data: { ...snapshot, version: 2 } });
  });
  expect(screen.getByText("Setup cancelled")).toBeTruthy();
  expect(screen.queryByLabelText("GitHub personal access token")).toBeNull();
});
