import { TooltipProvider } from "a13n-ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ResourceTable } from "../collection/resource-table";
import { ResourceReference } from "./resource-reference";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

it("reveals the resource ID and key on hover and copies each value", async () => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TooltipProvider>
        <ResourceReference id="agt_123" resourceKey="support-agent" />
      </TooltipProvider>
    </QueryClientProvider>,
  );

  await user.hover(
    screen.getByRole("button", { name: "Show resource reference" }),
  );
  expect(await screen.findByText("agt_123")).toBeTruthy();
  expect(screen.getByText("support-agent")).toBeTruthy();

  const key = screen.getByRole("button", { name: "Copy resource key" });
  expect(key.textContent).toBe("Key");
  await user.hover(key);
  expect(await screen.findByText("Click to copy")).toBeTruthy();
  await user.click(key);
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

it("keeps reference interactions inside the popover without activating its table row", async () => {
  const user = userEvent.setup();
  const activate = vi.fn();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ResourceTable
        items={[{ id: "sess_reference" }]}
        columns={[
          {
            label: "Session",
            render: (item) => (
              <>
                <span>Session name</span>
                <ResourceReference id={item.id} idLabel="Session ID" />
              </>
            ),
          },
        ]}
        onRowActivate={activate}
      />
    </QueryClientProvider>,
  );
  await user.click(
    screen.getByRole("button", { name: "Show resource reference" }),
  );
  const value = await screen.findByText("sess_reference");
  await user.click(value);
  await user.dblClick(value);
  expect(write).not.toHaveBeenCalled();
  await user.click(value.closest('[data-slot="popover-popup"]')!);
  expect(activate).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Copy resource ID" }));
  expect(write).toHaveBeenCalledWith("sess_reference");
  expect(activate).not.toHaveBeenCalled();
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByText("sess_reference")).toBeNull());
  await user.click(screen.getByText("Session name"));
  expect(activate).toHaveBeenCalledTimes(1);
  const row = screen.getByText("Session name").closest("tr")!;
  row.focus();
  await user.keyboard("{Enter}");
  expect(activate).toHaveBeenCalledTimes(2);
});
