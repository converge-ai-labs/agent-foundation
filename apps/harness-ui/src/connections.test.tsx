// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { ApiKeyConnection, SubscriptionLogin } from "./connections";
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  post: vi.fn(),
  remove: vi.fn(),
}));
vi.mock("./client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./client")>()),
  api: {
    GET: mocks.get,
    PUT: mocks.put,
    POST: mocks.post,
    DELETE: mocks.remove,
  },
  result: (value: { data: unknown }) => value.data,
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("saves a masked key separately and clears its input before publishing the reference", async () => {
  mocks.get.mockResolvedValue({ data: [] });
  mocks.put.mockResolvedValue({ data: { credential_ref: "key-custom" } });
  const select = vi.fn();
  render(<ApiKeyConnection selectedReference="key-custom" onSelect={select} />);
  const input = screen.getByLabelText("API key") as HTMLInputElement;
  expect(input.type).toBe("password");
  expect(
    (screen.getByLabelText("Credential reference") as HTMLInputElement).value,
  ).toBe("key-custom");
  fireEvent.change(input, { target: { value: "private-browser-key" } });
  fireEvent.click(screen.getByRole("button", { name: "Save / replace key" }));
  await waitFor(() => expect(select).toHaveBeenLastCalledWith("key-custom"));
  expect(input.value).toBe("");
  expect(mocks.put).toHaveBeenCalledWith("/api/auth/keys", {
    body: { credential_ref: "key-custom", key: "private-browser-key" },
  });
  expect(document.body.textContent).not.toContain("private-browser-key");
});

it("polls device presentation and refreshes the selected provider only after success", async () => {
  const initial = {
    session_id: "login-test",
    provider: "codex",
    method: "device",
    state: "starting",
    expires_in: 900,
  };
  mocks.post.mockResolvedValue({ data: initial });
  mocks.get
    .mockResolvedValueOnce({
      data: {
        ...initial,
        state: "waiting",
        verification_url: "https://example.test/device",
        user_code: "ABC-123",
      },
    })
    .mockResolvedValue({ data: { ...initial, state: "succeeded" } });
  const connected = vi.fn().mockResolvedValue(undefined);
  render(<SubscriptionLogin connected={connected} />);
  fireEvent.click(
    screen.getByRole("button", { name: "Connect codex with device code" }),
  );
  await screen.findByText("ABC-123", {}, { timeout: 2500 });
  expect(connected).not.toHaveBeenCalled();
  expect(
    (
      screen.getByRole("link", {
        name: "Open provider authorization page",
      }) as HTMLAnchorElement
    ).href,
  ).toBe("https://example.test/device");
  await waitFor(
    () => expect(connected).toHaveBeenCalledExactlyOnceWith("codex"),
    { timeout: 2500 },
  );
});

it("cancels the active authorization when setup leaves the connection step", async () => {
  const status = {
    session_id: "login-cancel",
    provider: "grok",
    method: "device",
    state: "waiting",
    expires_in: 900,
  };
  mocks.post.mockResolvedValue({ data: status });
  mocks.remove.mockResolvedValue({ data: { ...status, state: "cancelled" } });
  const view = render(<SubscriptionLogin connected={vi.fn()} />);
  fireEvent.click(
    screen.getByRole("button", { name: "Connect grok with device code" }),
  );
  await screen.findByRole("button", { name: "Cancel login" });
  view.unmount();
  expect(mocks.remove).toHaveBeenCalledWith("/api/auth/logins/{session_id}", {
    params: { path: { session_id: "login-cancel" } },
  });
});
