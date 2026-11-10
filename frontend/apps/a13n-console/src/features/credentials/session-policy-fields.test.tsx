import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { SessionPolicyFields } from "./session-policy-fields";
import type { SessionPolicy } from "./session-policy";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/ws_test",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
let cache: QueryClient;
afterEach(() => {
  cleanup();
  cache.clear();
  vi.resetAllMocks();
});
const initial: SessionPolicy = {
  working_directory: "/workspace",
  egress_mode: "controlled",
  egress: {
    destinations: {
      mode: "allowlist",
      hosts: ["api.github.com", "registry.npmjs.org"],
    },
    secrets: [
      {
        env: "GH_TOKEN",
        credential_id: "ecred_0123456789abcdef0123",
        inject_hosts: ["api.github.com"],
      },
    ],
  },
};
function mount(readOnly = false) {
  http.GET.mockResolvedValue({
    data: { items: [], next_cursor: null },
    response: new Response(),
  });
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const change = vi.fn();
  function Draft() {
    const [value, setValue] = useState(initial);
    return (
      <SessionPolicyFields
        value={value}
        onChange={(value) => {
          change(value);
          setValue(value);
        }}
        readOnly={readOnly}
      />
    );
  }
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <Draft />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), change };
}

it("retains unavailable saved references in read-only inspection without mutation actions", async () => {
  const { user, change } = mount(true);
  expect(await screen.findByText(/Unavailable credential/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Remove binding" })).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Edit Session JSON" }),
  ).toBeNull();
  await user.click(screen.getByRole("checkbox", { name: "api.github.com" }));
  expect(
    screen
      .getByRole("checkbox", { name: "api.github.com" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  await user.click(screen.getByRole("button", { name: /Agent usage preview/ }));
  expect(
    screen.getByText(
      'curl -H "Authorization: Bearer ${GH_TOKEN}" https://api.github.com/',
    ),
  ).toBeTruthy();
  expect(change).not.toHaveBeenCalled();
});

it("keeps malformed JSON in the dialog, discards cancellation and applies valid edits only to the draft", async () => {
  const { user, change } = mount();
  await user.click(screen.getByRole("button", { name: "Edit Session JSON" }));
  let dialog = screen.getByRole("dialog", {
    name: "Session configuration JSON",
  });
  fireEvent.change(within(dialog).getByRole("textbox"), {
    target: { value: '{"working_directory":123}' },
  });
  await user.click(
    within(dialog).getByRole("button", { name: "Apply to draft" }),
  );
  expect(within(dialog).getByRole("alert")).toBeTruthy();
  expect(change).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: "Session configuration JSON" }),
    ).toBeNull(),
  );
  await user.click(screen.getByRole("button", { name: "Edit Session JSON" }));
  dialog = screen.getByRole("dialog", { name: "Session configuration JSON" });
  expect(
    JSON.parse(
      (within(dialog).getByRole("textbox") as HTMLTextAreaElement).value,
    ),
  ).toEqual(initial);
  fireEvent.change(within(dialog).getByRole("textbox"), {
    target: {
      value: JSON.stringify({ ...initial, working_directory: "/project" }),
    },
  });
  await user.click(
    within(dialog).getByRole("button", { name: "Apply to draft" }),
  );
  expect(change).toHaveBeenCalledExactlyOnceWith({
    ...initial,
    working_directory: "/project",
  });
});
