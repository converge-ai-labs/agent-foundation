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
  await user.type(await screen.findByLabelText("Your name"), "Ada");
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

function renderAuthPage(path: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="*" element={<AuthPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

function renderAt(
  path: string,
  configuration: {
    email_delivery: boolean;
    password_enabled?: boolean;
    manual_password_reset?: boolean;
  },
) {
  http.GET.mockResolvedValue({
    data: { initialized: true, ...configuration },
    response: new Response(),
  });
  return renderAuthPage(path);
}

it("retries unavailable sign-in configuration without showing a password form", async () => {
  http.GET.mockRejectedValueOnce(
    new TypeError("network error"),
  ).mockResolvedValue({
    data: {
      initialized: true,
      password_enabled: false,
      oidc_providers: [
        { id: "team", label: "Team Identity", brand: "generic" },
      ],
    },
    response: new Response(),
  });
  const user = renderAuthPage("/login");

  expect(
    await screen.findByText("Couldn’t load sign-in options."),
  ).toBeTruthy();
  expect(screen.queryByLabelText("Password")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Try again" }));

  expect(
    await screen.findByRole("link", { name: "Sign in with {{provider}}" }),
  ).toBeTruthy();
  expect(screen.queryByLabelText("Password")).toBeNull();
});

it.each([true, false])(
  "offers the configured OIDC provider when password sign-in is %s",
  async (passwordEnabled) => {
    http.GET.mockResolvedValue({
      data: {
        initialized: true,
        password_enabled: passwordEnabled,
        email_delivery: true,
        oidc_providers: [
          { id: "team", label: "Team Identity", brand: "generic" },
        ],
      },
      response: new Response(),
    });
    renderAuthPage("/login");

    const provider = await screen.findByRole("link", {
      name: "Sign in with {{provider}}",
    });
    expect(provider.getAttribute("href")).toBe("/api/v1/auth/oidc/team/start");
    expect(Boolean(screen.queryByLabelText("Password"))).toBe(passwordEnabled);
    expect(
      Boolean(screen.queryByRole("link", { name: "Forgot password" })),
    ).toBe(passwordEnabled);
  },
);

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

it("directs users to their organization admin when only manual recovery is available", async () => {
  const user = renderAt("/login", {
    email_delivery: false,
    manual_password_reset: true,
  });
  await user.click(
    await screen.findByRole("link", { name: "Forgot password" }),
  );
  await screen.findByText(
    "Ask your organization administrator for a password reset link.",
  );
  expect(
    screen.queryByRole("button", { name: "Send recovery link" }),
  ).toBeNull();
});

it.each(["/forgot-password", "/reset-password#token=fixture"])(
  "does not offer password recovery on %s when password sign-in is disabled",
  async (path) => {
    renderAt(path, {
      password_enabled: false,
      email_delivery: false,
      manual_password_reset: false,
    });
    await screen.findByText(
      "This Service uses an identity provider. Go to sign in to continue.",
    );
    expect(screen.queryByLabelText("Password")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Send recovery link" }),
    ).toBeNull();
  },
);

it("clarifies recovery eligibility and fallback steps after submitting forgot password", async () => {
  http.POST.mockResolvedValue({
    response: new Response(null, { status: 204 }),
  });
  const user = renderAt("/forgot-password", { email_delivery: true });
  await user.type(
    await screen.findByLabelText("Email address"),
    "ada@example.com",
  );
  await user.click(screen.getByRole("button", { name: "Send recovery link" }));

  expect(await screen.findByText("Check your inbox")).toBeTruthy();
  expect(
    screen.getByText(
      "If this email belongs to an active local-password account, a recovery link will arrive shortly. Check spam, use your identity provider if you sign in with SSO, or contact your organization administrator if it does not arrive.",
    ),
  ).toBeTruthy();
});
