// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import type { components } from "../api.generated";
import { i18n } from "../i18n";
import { BrowserConfigurationFields } from "./browser-runtime";

afterEach(async () => {
  cleanup();
  await i18n.changeLanguage("en");
});
const configuration = {
  mode: "auto" as const,
  executable: null,
  vnc_executable: null,
};
function show(
  state: components["schemas"]["BrowserRuntimeState"],
  savedSource = "{}",
) {
  const client = {
    GET: vi.fn(async () => ({ data: state })),
    POST: vi.fn(async (url: string) => ({
      data: url.endsWith("/prepare")
        ? { ...state, status: "ready", error: null }
        : url.endsWith("/scan")
          ? { browsers: [], vncs: [] }
          : { supported: true, platform: "linux", requirements: [], steps: [] },
    })),
  };
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = (source: string, saved: string) => (
    <QueryClientProvider client={queries}>
      <TransportContext value={{ client } as unknown as Transport}>
        <BrowserConfigurationFields
          source={source}
          savedSource={saved}
          onChange={vi.fn()}
        />
      </TransportContext>
    </QueryClientProvider>
  );
  const rendered = render(view(savedSource, savedSource));
  return {
    client,
    rerender: (source: string, saved: string) =>
      rendered.rerender(view(source, saved)),
  };
}

it("reads runtime status and retries explicitly without allocating a Thread", async () => {
  const { client } = show({
    status: "failed",
    platform: "linux",
    available: true,
    configuration,
    error: "Profile is in use",
  });
  await screen.findByText("Profile is in use");
  expect(client.POST.mock.calls.some(([url]) => url.endsWith("/prepare"))).toBe(
    false,
  );
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(screen.getByText("Profile is in use")).toBeTruthy();
  await act(() => i18n.changeLanguage("en"));
  fireEvent.click(
    screen.getByRole("button", { name: "Retry browser startup" }),
  );
  await screen.findByText("Ready");
  expect(
    client.POST.mock.calls.filter(([url]) => url.endsWith("/prepare")),
  ).toHaveLength(1);
  expect(
    client.POST.mock.calls.every(([url]) => url.startsWith("/api/browser/")),
  ).toBe(true);
  expect(
    screen
      .getByRole("button", { name: "Retry browser startup" })
      .hasAttribute("disabled"),
  ).toBe(true);
});

it("distinguishes active configuration, saved restart requirements and unsaved edits", async () => {
  const { rerender } = show({
    status: "ready",
    platform: "linux",
    available: true,
    configuration,
  });
  await screen.findByText("Ready");
  const notice =
    "Saved browser settings require an App restart. Retry uses the active configuration.";
  const changed = "browser: {executable: /next/chrome}";
  rerender(changed, "{}");
  expect(screen.queryByText(notice)).toBeNull();
  rerender(changed, changed);
  expect(screen.getByText(notice)).toBeTruthy();
  fireEvent.click(screen.getByText("Active configuration"));
  expect(screen.getAllByText("Automatic").length).toBeGreaterThan(0);
  expect(screen.queryByText("/next/chrome")).toBeNull();
});

it("keeps disabled sharing read-only without scanning or starting the browser", async () => {
  const { client } = show({
    status: "disabled",
    platform: "linux",
    available: false,
    configuration,
    error: "Computer sharing is off.",
  });
  await waitFor(() => expect(client.GET).toHaveBeenCalledOnce());
  expect(client.POST).not.toHaveBeenCalled();
  expect(
    screen
      .getByRole("button", { name: "Retry browser startup" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(screen.queryByRole("button", { name: "Scan" })).toBeNull();
});
