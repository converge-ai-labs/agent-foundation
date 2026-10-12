// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../service-client";
import { AuthPage } from "./pages";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("./context", () => ({
  useClient: () => ({ http }),
  useAuth: () => ({ anonymous: true }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  window.location.hash = "";
});

it("asks for the existing account's password when the invited email already has one", async () => {
  window.location.hash = "#token=secret";
  http.GET.mockResolvedValue({
    data: { initialized: true, email_delivery: false },
    response: new Response(),
  });
  http.POST.mockRejectedValue(
    new ApiError(401, "unauthenticated", "Incorrect password", {}, null),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/invitations/inv_team/accept"]}>
        <Routes>
          <Route
            path="/invitations/:invitationId/accept"
            element={<AuthPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Your name"), "Ada");
  await user.type(screen.getByLabelText("Password"), "not-my-password");
  await user.click(screen.getByRole("button", { name: "Accept invitation" }));

  const password = screen.getByLabelText("Password");
  expect(
    await screen.findByText(
      "The invited email already has an account. Enter its password.",
    ),
  ).toBeTruthy();
  expect(password.getAttribute("aria-invalid")).toBe("true");
  expect(document.activeElement).toBe(password);
});
