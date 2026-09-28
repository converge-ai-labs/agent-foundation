import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Security } from "./security";

const client = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useAuth: () => ({
    data: { user: { value: { email: "alex@example.com" }, etag: '"v1"' } },
  }),
  useClient: () => ({ http: client, workspace: () => client }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
beforeEach(() => {
  client.GET.mockResolvedValue({ data: { email_delivery: true } });
  client.POST.mockResolvedValue({});
  client.PATCH.mockResolvedValue({});
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function setup() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Security />
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

it("explains unavailable email delivery without exposing an unusable form", async () => {
  client.GET.mockResolvedValue({ data: { email_delivery: false } });
  const user = setup();
  await screen.findByText(
    "Email delivery is not configured. Contact your organization administrator.",
  );
  expect(
    (screen.getByRole("button", { name: "Change email" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "Change email" }));
  expect(screen.queryByLabelText("New email address")).toBeNull();
  expect(screen.getByRole("region", { name: "Password" })).toBeTruthy();
});

it("reveals email editing inline and submits only the email form", async () => {
  const user = setup();
  const section = within(screen.getByRole("region", { name: "Email address" }));
  await waitFor(() =>
    expect(
      (
        section.getByRole("button", {
          name: "Change email",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  await user.click(section.getByRole("button", { name: "Change email" }));
  await user.clear(section.getByLabelText("New email address"));
  await user.type(
    section.getByLabelText("New email address"),
    "new@example.com",
  );
  await user.type(
    section.getByLabelText("Current password"),
    "preview-password",
  );
  await user.click(
    section.getByRole("button", { name: "Send verification email" }),
  );
  await screen.findByRole("status");
  expect(client.PATCH).toHaveBeenCalledExactlyOnceWith("/api/v1/users/me", {
    headers: { "If-Match": '"v1"' },
    body: { email: "new@example.com", current_password: "preview-password" },
  });
  expect(section.queryByLabelText("New email address")).toBeNull();
  expect(section.getByText("alex@example.com")).toBeTruthy();
});

it("preserves the email draft after a recoverable request failure", async () => {
  client.PATCH.mockRejectedValue(
    new Error("Email verification is temporarily unavailable."),
  );
  const user = setup();
  const section = within(screen.getByRole("region", { name: "Email address" }));
  await waitFor(() =>
    expect(
      (
        section.getByRole("button", {
          name: "Change email",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  await user.click(section.getByRole("button", { name: "Change email" }));
  await user.type(
    section.getByLabelText("Current password"),
    "preview-password",
  );
  await user.click(
    section.getByRole("button", { name: "Send verification email" }),
  );
  await screen.findByText("Email verification is temporarily unavailable.");
  expect(
    (section.getByLabelText("New email address") as HTMLInputElement).value,
  ).toBe("alex@example.com");
  await user.click(section.getByRole("button", { name: "Cancel" }));
  await user.click(section.getByRole("button", { name: "Change email" }));
  expect(
    (section.getByLabelText("Current password") as HTMLInputElement).value,
  ).toBe("");
});

it("opens password fields only in a dialog and clears a cancelled draft", async () => {
  const user = setup();
  expect(screen.queryByLabelText("Current password")).toBeNull();
  const trigger = screen.getByRole("button", { name: "Change password" });
  await user.click(trigger);
  const dialog = within(
    screen.getByRole("dialog", { name: "Change password" }),
  );
  await user.type(dialog.getByLabelText("Current password"), "old-password");
  await user.type(dialog.getByLabelText("New password"), "new-long-password");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(document.activeElement).toBe(trigger);
  await user.click(trigger);
  expect(
    (screen.getByLabelText("Current password") as HTMLInputElement).value,
  ).toBe("");
  expect(
    (screen.getByLabelText("New password") as HTMLInputElement).value,
  ).toBe("");
});
it("submits password changes from the dialog and keeps this session", async () => {
  const user = setup();
  await user.click(screen.getByRole("button", { name: "Change password" }));
  const dialog = within(
    screen.getByRole("dialog", { name: "Change password" }),
  );
  await user.type(dialog.getByLabelText("Current password"), "old-password");
  await user.type(dialog.getByLabelText("New password"), "new-long-password");
  await user.click(dialog.getByRole("button", { name: "Change password" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(client.POST).toHaveBeenCalledExactlyOnceWith(
    "/api/v1/users/me/password",
    {
      body: { current_password: "old-password", password: "new-long-password" },
    },
  );
});
