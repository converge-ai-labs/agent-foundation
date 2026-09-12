// @vitest-environment jsdom
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { ConnectorSetupCallback } from "./callback";

const mocks = vi.hoisted(() => ({
  POST: vi.fn(),
  clear: vi.fn(),
  method: "oauth_verifier",
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: { POST: mocks.POST } }),
  useAuth: () => ({ isPending: false, anonymous: false, error: null }),
}));
vi.mock("./authorization-context", () => ({
  takeCallbackSession: () => "single-use-session",
  readAuthorization: () => ({
    attempt_id: "csa_test",
    browser_nonce: "b".repeat(64),
    workspace_id: "ws_test",
    return_path: "/workspace/design/connections",
    connection_id: "cconn_test",
    connection_name: "Review account",
    workspace_name: "Design",
    completion_method: mocks.method,
  }),
  clearAuthorization: mocks.clear,
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

it("redeems once under StrictMode and never retries an uncertain response", async () => {
  mocks.POST.mockRejectedValue(new Error("Verification is being reconciled."));
  render(
    <StrictMode>
      <MemoryRouter>
        <ConnectorSetupCallback />
      </MemoryRouter>
    </StrictMode>,
  );
  await screen.findByText("Verification is being reconciled.");
  await waitFor(() => expect(mocks.POST).toHaveBeenCalledTimes(1));
  expect(mocks.POST).toHaveBeenCalledWith("/api/v1/connector-setup/complete", {
    body: {
      attempt_id: "csa_test",
      browser_nonce: "b".repeat(64),
      session_uri: "single-use-session",
    },
  });
  expect(mocks.clear).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole("link", { name: "Continue" }).getAttribute("href"),
  ).toBe("/workspace/design/connections?connection=cconn_test");
});

it("requires an explicit confirmation for a non-OAuth return", async () => {
  mocks.POST.mockClear();
  mocks.method = "browser_confirmation";
  mocks.POST.mockRejectedValue(new Error("Still pending."));
  render(
    <MemoryRouter>
      <ConnectorSetupCallback />
    </MemoryRouter>,
  );
  const button = await screen.findByRole("button", {
    name: "Confirm connection",
  });
  expect(mocks.POST).not.toHaveBeenCalled();
  expect(screen.getByText(/Composio cannot verify which browser/)).toBeTruthy();
  fireEvent.click(button);
  await screen.findByText("Still pending.");
  expect(mocks.POST).toHaveBeenCalledExactlyOnceWith(
    "/api/v1/connector-setup/complete",
    {
      body: { attempt_id: "csa_test", browser_nonce: "b".repeat(64) },
    },
  );
});
