// @vitest-environment jsdom
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { ConnectorSetupCallback } from "./callback";

const mocks = vi.hoisted(() => ({ POST: vi.fn(), clear: vi.fn() }));
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
    return_path: "/acme/design/connectors",
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
  ).toBe("/acme/design/connectors");
});
