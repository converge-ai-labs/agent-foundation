import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import {
  matchesTarget,
  TargetPicker,
  type TargetOption,
} from "./target-picker";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const west: TargetOption = {
  value: "environment:env_west",
  name: "West worker",
  kind: "environment",
  status: "ready",
  source: "mds_local",
  labels: { region: "west", team: "infra" },
  deviceId: "host-west",
};
const device: TargetOption = {
  value: "device:dev_east",
  name: "East laptop",
  kind: "device",
  status: "disabled",
  source: "",
  labels: {},
  deviceId: "host-east",
};
it("combines inventory facets with case-insensitive name, identity and label terms", () => {
  const filters = {
    search: "WORKER region=west",
    kind: "environment",
    status: "ready",
    source: "mds_local",
  };
  expect(matchesTarget(west, filters)).toBe(true);
  for (const change of [
    { search: "region=east" },
    { kind: "device" },
    { status: "disabled" },
    { source: "none" },
  ])
    expect(matchesTarget(west, { ...filters, ...change })).toBe(false);
  expect(
    matchesTarget(device, {
      search: "host-east",
      kind: "device",
      status: "disabled",
      source: "none",
    }),
  ).toBe(true);
});
it("searches, clears empty results and selects a target without changing a hidden selection", async () => {
  const user = userEvent.setup(),
    change = vi.fn();
  render(
    <TargetPicker
      items={[west, device]}
      sources={[]}
      value={west.value}
      onChange={change}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Target: West worker" }));
  const search = screen.getByRole("searchbox", { name: "Search targets" });
  await user.type(search, "missing");
  expect(screen.getByText("No matching targets")).toBeTruthy();
  expect(change).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Clear filters" }));
  await user.type(search, "host-east");
  expect(screen.queryByRole("button", { name: /West worker/ })).toBeNull();
  await user.click(screen.getByRole("button", { name: /East laptop/ }));
  expect(change).toHaveBeenCalledWith(device.value);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
