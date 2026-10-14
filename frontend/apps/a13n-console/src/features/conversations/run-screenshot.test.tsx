import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RunScreenshot } from "./run-screenshot";

const mocks = vi.hoisted(() => {
  const GET = vi.fn();
  return { GET, client: { workspace: () => ({ GET }) }, revalidate: vi.fn() };
});
vi.mock("../../auth/context", () => ({
  useClient: () => mocks.client,
  revalidateSession: mocks.revalidate,
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_one" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

beforeEach(() => {
  mocks.GET.mockReset();
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn(() => "blob:screenshot"),
    revokeObjectURL: vi.fn(),
  });
});
afterEach(() => vi.unstubAllGlobals());

it("loads only on request through the workspace API and releases bytes on close", async () => {
  mocks.GET.mockResolvedValue({
    data: new Blob(["png"], { type: "image/png" }),
  });
  const user = userEvent.setup();
  render(<RunScreenshot runId="run_one" imageId="cnt_one" />);
  expect(mocks.GET).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "View screenshot" }));
  const image = await screen.findByRole("img", { name: "Computer screenshot" });
  expect(image.getAttribute("src")).toBe("blob:screenshot");
  expect(screen.getByText(/not a live desktop/)).toBeTruthy();
  expect(mocks.GET).toHaveBeenCalledWith(
    "/api/v1/runs/{run_id}/images/{image_id}",
    expect.objectContaining({
      params: { path: { run_id: "run_one", image_id: "cnt_one" } },
      parseAs: "blob",
    }),
  );
  await user.keyboard("{Escape}");
  await waitFor(() =>
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:screenshot"),
  );
});

it("keeps an uncommitted image retryable without pretending it is visible", async () => {
  mocks.GET.mockRejectedValue(new Error("Screenshot not yet committed"));
  const user = userEvent.setup();
  render(<RunScreenshot runId="run_one" imageId="cnt_one" />);
  await user.click(screen.getByRole("button", { name: "View screenshot" }));
  await screen.findByText("Screenshot not yet committed");
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.getByText(/may not have been saved yet/)).toBeTruthy();
});
