import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ResourceReference } from "./resource-reference";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

it("reveals the resource ID and key on hover and copies each value", async () => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ResourceReference id="agt_123" resourceKey="support-agent" />
    </QueryClientProvider>,
  );

  await user.hover(
    screen.getByRole("button", { name: "Show resource reference" }),
  );
  expect(await screen.findByText("agt_123")).toBeTruthy();
  expect(screen.getByText("support-agent")).toBeTruthy();

  await user.click(screen.getByRole("button", { name: "Copy resource key" }));
  expect(write).toHaveBeenCalledWith("support-agent");
});

it("closes after leaving a hovered reference and can be opened again", async () => {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ResourceReference id="agt_hover" />
      <button type="button">Outside</button>
    </QueryClientProvider>,
  );
  const trigger = screen.getByRole("button", {
    name: "Show resource reference",
  });
  await user.hover(trigger);
  await screen.findByText("agt_hover");
  await user.hover(screen.getByRole("button", { name: "Outside" }));
  await waitFor(() => expect(screen.queryByText("agt_hover")).toBeNull());
  await user.hover(trigger);
  await screen.findByText("agt_hover");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByText("agt_hover")).toBeNull());
});
