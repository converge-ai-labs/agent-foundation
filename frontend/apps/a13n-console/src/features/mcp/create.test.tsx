import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { CreateMCP } from "./create";
import { mcpPresets } from "../connections/presets";

const http = vi.hoisted(() => ({ POST: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => true }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it.each(["google-compute-engine", "jentic"])(
  "%s prefills header names and sends values only through the credential endpoint",
  async (presetId) => {
    const preset = mcpPresets.find((item) => item.id === presetId)!;
    const headers = preset.headerNames!;
    const initial = {
      id: "mcp_test",
      version: 1,
      credential_configured: false,
    };
    const configured = { ...initial, version: 2, credential_configured: true };
    const ready = { ...configured, version: 3, status: "ready" };
    http.POST.mockImplementation(async (path: string) => ({
      data: path.endsWith("/credentials")
        ? configured
        : path.endsWith("/reconnect")
          ? ready
          : initial,
      response: new Response(),
    }));
    const cache = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const onSuccess = vi.fn();
    render(
      <QueryClientProvider client={cache}>
        <CreateMCP
          onCancel={vi.fn()}
          preset={preset}
          onStarted={vi.fn()}
          onSuccess={onSuccess}
        />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    const values: Record<string, string> = {};
    for (const [index, name] of headers.entries()) {
      expect(
        (screen.getByLabelText(`Header name ${index + 1}`) as HTMLInputElement)
          .value,
      ).toBe(name);
      const value =
        name === "Authorization" ? "Bearer test-token" : `value-${index}`;
      values[name.toLowerCase()] = value;
      await user.type(
        screen.getByLabelText(`Header value ${index + 1}`),
        value,
      );
    }
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(ready));
    const create = http.POST.mock.calls.find(([path]) =>
      path.includes("/workspaces/"),
    )!;
    expect(create[1].body).toEqual({
      name: preset.name,
      endpoint_url: preset.endpoint,
      auth_mode: "static_headers",
      static_header_names: headers.map((name) => name.toLowerCase()),
    });
    const credentials = http.POST.mock.calls.find(([path]) =>
      path.endsWith("/credentials"),
    )!;
    expect(credentials[1].body).toEqual({
      expected_version: 1,
      static_headers: values,
    });
    cache.clear();
  },
);
