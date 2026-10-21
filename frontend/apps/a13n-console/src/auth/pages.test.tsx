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

function renderAt(path: string, configuration: { email_delivery: boolean }) {
  http.GET.mockResolvedValue({
    data: { initialized: true, ...configuration },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={path} element={<AuthPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

it("validates sign-in under each field instead of sending an empty request", async () => {
  const user = renderAt("/login", { email_delivery: true });
  await user.click(await screen.findByRole("button", { name: "Sign in" }));

  expect(screen.getByText("Enter your email address.")).toBeTruthy();
  expect(screen.getByText("Enter your password.")).toBeTruthy();
  expect(document.activeElement).toBe(screen.getByLabelText("Email address"));
  expect(http.POST).not.toHaveBeenCalled();

  await user.type(screen.getByLabelText("Email address"), "ada");
  expect(screen.getByText("Enter a valid email address.")).toBeTruthy();
});

it("explains unavailable password recovery without offering a form it cannot send", async () => {
  renderAt("/forgot-password", { email_delivery: false });
  expect(
    await screen.findByText(
      "Email delivery is not configured. Contact your organization administrator.",
    ),
  ).toBeTruthy();
  expect(screen.queryByLabelText("Email address")).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Send recovery link" }),
  ).toBeNull();
  expect(screen.getByRole("link", { name: "Back to sign in" })).toBeTruthy();
});
