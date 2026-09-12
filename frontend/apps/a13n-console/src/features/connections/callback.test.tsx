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
import { ConnectionAuthorizationCallback } from "./callback";
const mocks = vi.hoisted(() => ({ POST: vi.fn(), clear: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: { POST: mocks.POST } }),
  useAuth: () => ({ isPending: false, anonymous: false, error: null }),
}));
vi.mock("./authorization-context", () => ({
  takeCallback: () => ({
    receipt: "r".repeat(48),
    state: "a".repeat(64),
    authorizationId: "auth_test",
  }),
  readAuthorization: () => ({
    authorizationId: "auth_test",
    state: "a".repeat(64),
    verifier: "b".repeat(64),
    workspaceId: "ws_test",
    returnPath: "/workspace/design/connections",
    connectionId: "conn_test",
  }),
  clearAuthorization: mocks.clear,
  requireCompletedAuthorization: (value: unknown) => value,
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);
it("redeems once under StrictMode and directs an uncertain completion to connection status", async () => {
  mocks.POST.mockRejectedValue(new Error("Verification is being reconciled."));
  render(
    <StrictMode>
      <MemoryRouter>
        <ConnectionAuthorizationCallback />
      </MemoryRouter>
    </StrictMode>,
  );
  expect(mocks.POST).not.toHaveBeenCalled();
  fireEvent.click(
    await screen.findByRole("button", { name: "Complete authorization" }),
  );
  await screen.findByText("Verification is being reconciled.");
  await waitFor(() => expect(mocks.POST).toHaveBeenCalledTimes(1));
  expect(mocks.POST).toHaveBeenCalledWith(
    "/api/v1/connection-authorizations/{authorization_id}/complete",
    {
      params: { path: { authorization_id: "auth_test" } },
      body: { receipt: "r".repeat(48), completion_verifier: "b".repeat(64) },
    },
  );
  expect(mocks.clear).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole("link", { name: "Continue" }).getAttribute("href"),
  ).toBe("/workspace/design/connections?connection=conn_test");
});
