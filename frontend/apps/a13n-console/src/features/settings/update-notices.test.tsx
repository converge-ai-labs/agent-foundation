import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { UpdateNotices } from "./update-notices";

const get = vi.hoisted(() => vi.fn());
vi.mock("../../auth/context", () => ({
  useAuth: () => ({ data: { user: { value: { id: "user_1" } } } }),
  useClient: () => ({ http: { GET: get } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, vars?: { version: string }) =>
      key.replace("{{version}}", vars?.version ?? ""),
  }),
}));

afterEach(() => {
  cleanup();
  localStorage.clear();
  get.mockReset();
});

const releaseUrl =
  "https://github.com/converge-ai-labs/agent-foundation/releases/tag/release/a13n-service-v1.10.0";
const advisoryUrl =
  "https://github.com/converge-ai-labs/agent-foundation/security/advisories/GHSA-2222-3333-4444";
const snapshot: Schema["UpdateNotices"] = {
  current_version: "1.9.0",
  latest_stable: { version: "1.10.0", url: releaseUrl, is_newer: true },
  advisories: [
    {
      id: "GHSA-2222-3333-4444",
      summary: "Example <strong>security advisory</strong>",
      updated_at: "2026-01-01T00:00:00Z",
      severity: "high",
      url: advisoryUrl,
    },
  ],
};

function setup(value: Schema["UpdateNotices"], organizationId = "org_1") {
  get.mockResolvedValue({ data: value });
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <UpdateNotices organizationId={organizationId} />
    </QueryClientProvider>,
  );
}

it("links directly to GitHub, renders the advisory as text, and hides only that revision locally", async () => {
  const user = userEvent.setup();
  const first = setup(snapshot);
  const banner = await screen.findByRole("alert");
  expect(
    within(banner).getByText(snapshot.advisories[0]!.summary),
  ).toBeTruthy();
  expect(banner.querySelector("strong")).toBeNull();
  const advisory = within(banner).getByRole("link", {
    name: "View advisory on GitHub",
  });
  expect(advisory.getAttribute("href")).toBe(advisoryUrl);
  expect(advisory.getAttribute("target")).toBe("_blank");
  const release = screen.getByRole("link", { name: "v1.10.0 available" });
  expect(release.getAttribute("href")).toBe(releaseUrl);
  expect(release.getAttribute("target")).toBe("_blank");
  await user.click(
    within(banner).getByRole("button", { name: "Hide on this browser" }),
  );
  expect(screen.queryByRole("alert")).toBeNull();
  first.unmount();

  const again = setup(snapshot);
  await screen.findByRole("link", { name: "v1.10.0 available" });
  expect(screen.queryByRole("alert")).toBeNull();
  again.unmount();

  const otherOrg = setup(snapshot, "org_2");
  await screen.findByRole("alert");
  otherOrg.unmount();

  setup({
    ...snapshot,
    advisories: [
      { ...snapshot.advisories[0]!, updated_at: "2026-01-02T00:00:00Z" },
    ],
  });
  expect(await screen.findByRole("alert")).toBeTruthy();
});
