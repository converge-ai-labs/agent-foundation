import {
  QueryClient,
  QueryClientProvider,
  QueryObserver,
} from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { ConnectComputer, pairingCommand } from "./connect-computer";
import type { Schema } from "../../shared/api";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test", name: "My workspace" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

const invitation: Schema["PairingCreated"] = {
  id: "pair_test",
  workspace_id: "ws_test",
  owner_id: "user_test",
  host_name: "console-test",
  service_url: "https://console.example",
  expires_at: "2099-01-01T00:00:00Z",
  state: "waiting",
  launcher_url:
    "a13n-envd://connect?service=https%3A%2F%2Fconsole.example&workspace=ws_test&host=console-test&pairing=pair_test",
};
const claimed = {
  ...invitation,
  state: "claimed",
  verification_code: "123456",
  device: {
    name: "My Mac",
    device_id: "dev_test",
    default_working_directory: "/Users/test/project",
    computer_use: true,
    full_control: true,
  },
};

beforeEach(() => {
  vi.spyOn(window, "open").mockImplementation(() => null);
  http.GET.mockReset().mockResolvedValue({ data: claimed });
  http.POST.mockReset().mockImplementation(async (path: string) => ({
    data: path.endsWith("/approve")
      ? { ...claimed, state: "approved", environment_id: "env_test" }
      : path.endsWith("/cancel")
        ? { ...claimed, state: "cancelled" }
        : invitation,
  }));
});

async function setup() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={cache}>
      <ConnectComputer />
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Connect my computer" }));
  return { cache, user };
}

it("opens Envd to check registration and requires a code only for a new device", async () => {
  const { cache, user } = await setup();
  expect(
    (await screen.findByRole("link", { name: "Open A13N Envd" })).getAttribute(
      "href",
    ),
  ).toBe(invitation.launcher_url);
  await screen.findByText("My Mac");
  expect(screen.getByText("/Users/test/project")).toBeTruthy();
  const approve = screen.getByRole("button", { name: "Approve computer" });
  expect((approve as HTMLButtonElement).disabled).toBe(true);
  expect(http.POST).toHaveBeenCalledTimes(1);
  expect(window.open).toHaveBeenCalledWith(invitation.launcher_url, "_self");
  await user.type(screen.getByLabelText("Verification code"), "123456");
  http.GET.mockImplementation(async (path: string) => ({
    data: path.includes("/pairings/")
      ? claimed
      : {
          environment: {
            name: "My Mac",
            status: "ready",
            client_connection: { state: "offline" },
          },
        },
  }));
  await user.click(approve);
  await screen.findByText("Computer approved");
  expect(http.POST).toHaveBeenLastCalledWith(
    "/api/v1/client-environments/pairings/{pairing_id}/approve",
    {
      params: { path: { pairing_id: "pair_test" } },
      body: { verification_code: "123456" },
    },
  );
  expect(screen.getByText("state.offline")).toBeTruthy();
  cache.clear();
});

it("cancels a pending pairing and offers a new request", async () => {
  const { cache, user } = await setup();
  await user.click(
    await screen.findByRole("button", { name: "Cancel connection" }),
  );
  await screen.findByText(
    "This pairing is no longer active. Create a new request to try again.",
  );
  expect(http.POST).toHaveBeenLastCalledWith(
    "/api/v1/client-environments/pairings/{pairing_id}/cancel",
    {
      params: { path: { pairing_id: "pair_test" } },
    },
  );
  cache.clear();
});

it.each([false, true])(
  "refreshes the active instance list when the device becomes online (dialog closed: %s)",
  async (closeDialog) => {
    const { cache, user } = await setup();
    let state = "offline";
    const fetchList = vi.fn(async () => state);
    const list = new QueryObserver(cache, {
      queryKey: ["environments", "ws_test"],
      queryFn: fetchList,
    });
    const unsubscribe = list.subscribe(() => {});
    await waitFor(() => expect(list.getCurrentResult().data).toBe("offline"));
    await user.type(
      await screen.findByLabelText("Verification code"),
      "123456",
    );
    let connected!: () => void;
    const pending = new Promise<void>((resolve) => {
      connected = resolve;
    });
    http.GET.mockImplementation(async (path: string) => {
      if (path.includes("/pairings/")) return { data: claimed };
      await pending;
      return {
        data: {
          environment: {
            id: "env_test",
            name: "My Mac",
            status: "ready",
            client_connection: { state: "online" },
          },
        },
      };
    });
    await user.click(screen.getByRole("button", { name: "Approve computer" }));
    await screen.findByText("Computer approved");
    await waitFor(() => expect(fetchList).toHaveBeenCalledTimes(2));
    expect(list.getCurrentResult().data).toBe("offline");
    if (closeDialog) {
      await user.keyboard("{Escape}");
      await waitFor(() =>
        expect(screen.queryByText("Computer approved")).toBeNull(),
      );
    }
    state = "online";
    await act(async () => {
      connected();
    });
    await waitFor(() => expect(list.getCurrentResult().data).toBe("online"));
    expect(fetchList).toHaveBeenCalledTimes(3);
    // Further observations of the same online connection do not refresh the list again.
    await act(async () => {
      await cache.invalidateQueries({ queryKey: ["paired-environment"] });
    });
    expect(fetchList).toHaveBeenCalledTimes(3);
    unsubscribe();
    cache.clear();
  },
);

it("keeps a failed approval visible without claiming the computer is connected", async () => {
  const { cache, user } = await setup();
  await user.type(await screen.findByLabelText("Verification code"), "999999");
  http.POST.mockRejectedValue(
    new Error("The verification code does not match"),
  );
  await user.click(screen.getByRole("button", { name: "Approve computer" }));
  await waitFor(() =>
    expect(
      screen.getByText("The verification code does not match"),
    ).toBeTruthy(),
  );
  expect(screen.queryByText("Computer approved")).toBeNull();
  cache.clear();
});

it("quotes copied command values and includes no API credential", () => {
  const command = pairingCommand({ ...invitation, host_name: "a'b" });
  expect(command).toContain("--host 'a'\\''b'");
  expect(command).toContain("--pairing-id 'pair_test'");
  expect(command).not.toContain("--api-token-file");
});

it.each(["online", "offline"])(
  "recognizes an existing computer after a fresh page mount (%s)",
  async (state) => {
    http.GET.mockImplementation(async (path: string) => ({
      data: path.includes("/pairings/")
        ? {
            ...invitation,
            state: "approved",
            reused: true,
            environment_id: "env_existing",
          }
        : {
            environment: {
              id: "env_existing",
              name: "Existing Mac",
              status: "ready",
              client_connection: { state },
            },
          },
    }));
    const { cache } = await setup();
    await screen.findByText("Computer already registered");
    await screen.findByText("Existing Mac");
    expect(screen.getByText(`state.${state}`)).toBeTruthy();
    expect(screen.queryByLabelText("Verification code")).toBeNull();
    expect(http.POST).toHaveBeenCalledTimes(1);
    expect(http.POST).toHaveBeenCalledWith(
      "/api/v1/client-environments/pairings",
    );
    cache.clear();
  },
);
