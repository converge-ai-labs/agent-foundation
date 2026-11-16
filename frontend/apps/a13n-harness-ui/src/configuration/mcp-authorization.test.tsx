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
import { McpAuthorization } from "./mcp-authorization";
import { i18n } from "../i18n";

const api = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  DELETE: vi.fn(),
}));
vi.mock("../transport/context", () => ({
  useTransport: () => ({ client: api }),
}));
afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
});
const target = { kind: "mcp" as const, id: "mcp-linear" };
const waiting = {
  state: "disconnected",
  login: {
    attempt_id: "attempt",
    state: "waiting",
    authorization_url: "https://auth.example/authorize?state=opaque",
  },
};
function mount(value: object = { state: "disconnected" }) {
  api.GET.mockResolvedValue({ data: value });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const connected = vi.fn();
  const view = render(
    <QueryClientProvider client={cache}>
      <McpAuthorization target={target} onConnected={connected} />
    </QueryClientProvider>,
  );
  return { ...view, cache, connected };
}
it("observes authorization without starting login and exposes explicit continuation after reopening", async () => {
  const { unmount } = mount(waiting);
  const link = await screen.findByRole("link", {
    name: "Continue authorization",
  });
  expect(link.getAttribute("href")).toBe(waiting.login.authorization_url);
  expect(api.POST).not.toHaveBeenCalled();
  unmount();
  mount(waiting);
  expect(
    await screen.findByRole("link", { name: "Continue authorization" }),
  ).toBeTruthy();
  expect(api.POST).not.toHaveBeenCalled();
});
it("starts only on a gesture and keeps continuation usable when a popup is blocked", async () => {
  vi.spyOn(window, "open").mockReturnValue(null);
  api.POST.mockImplementation(async () => {
    api.GET.mockResolvedValue({ data: waiting });
    return { data: waiting.login };
  });
  mount();
  const button = await screen.findByRole("button", { name: "Connect" });
  await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  fireEvent.click(button);
  expect(
    await screen.findByRole("link", { name: "Continue authorization" }),
  ).toBeTruthy();
  expect(api.POST).toHaveBeenCalledTimes(1);
  expect(api.POST).toHaveBeenCalledWith("/api/mcp-authorization/start", {
    body: target,
  });
});
it("does not retry an uncertain login request and refreshes saved status before another decision", async () => {
  vi.spyOn(window, "open").mockReturnValue(null);
  api.POST.mockRejectedValue(new Error("Response lost"));
  mount();
  const button = await screen.findByRole("button", { name: "Connect" });
  await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  fireEvent.click(button);
  await screen.findByText(
    "The login request outcome is unknown. Refresh status before starting again.",
  );
  expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  api.GET.mockResolvedValue({ data: waiting });
  fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
  expect(
    await screen.findByRole("link", { name: "Continue authorization" }),
  ).toBeTruthy();
  expect(api.POST).toHaveBeenCalledTimes(1);
});
it("language switches preserve pending authorization and cancellation is explicit", async () => {
  api.DELETE.mockImplementation(async () => {
    api.GET.mockResolvedValue({ data: { state: "disconnected" } });
    return { data: undefined, response: new Response(null, { status: 204 }) };
  });
  mount(waiting);
  await screen.findByRole("link", { name: "Continue authorization" });
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(
    screen.getByRole("link", { name: "继续授权" }).getAttribute("href"),
  ).toBe(waiting.login.authorization_url);
  expect(api.POST).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "取消授权" }));
  await waitFor(() =>
    expect(api.DELETE).toHaveBeenCalledWith(
      "/api/mcp-authorization/attempts/{attempt_id}",
      { params: { path: { attempt_id: "attempt" } } },
    ),
  );
});
