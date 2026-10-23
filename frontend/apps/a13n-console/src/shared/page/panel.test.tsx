import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Button, ModalFrame } from "a13n-ui";
import { Panel } from "./panel";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

function Inspector() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Inspect</button>
      <Panel open={open} title="Record details" onClose={() => setOpen(false)}>
        <input aria-label="Note" />
        <ModalFrame
          title="Nested details"
          closeLabel="Close dialog"
          trigger={<Button>More details</Button>}
        >
          <p>Additional information</p>
        </ModalFrame>
      </Panel>
    </>
  );
}

it("moves focus into the inspector, preserves editing on Escape, and returns focus on close", async () => {
  const user = userEvent.setup();
  render(<Inspector />);
  const trigger = screen.getByRole("button", { name: "Inspect" });
  await user.click(trigger);
  expect(document.activeElement?.textContent).toBe("Record details");
  await user.click(screen.getByRole("textbox", { name: "Note" }));
  await user.type(screen.getByRole("textbox"), "Keep this draft{Escape}");
  expect(screen.getByRole("textbox").getAttribute("aria-label")).toBe("Note");
  await user.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("complementary")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

it("lets a nested dialog consume Escape without closing the inspector", async () => {
  const user = userEvent.setup();
  render(<Inspector />);
  await user.click(screen.getByRole("button", { name: "Inspect" }));
  await user.click(screen.getByRole("button", { name: "More details" }));
  await screen.findByRole("dialog");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByRole("complementary")).toBeTruthy();
  // Default-prevented keys belong to the nested control as well.
  const escape = new KeyboardEvent("keydown", {
    key: "Escape",
    bubbles: true,
    cancelable: true,
  });
  escape.preventDefault();
  fireEvent(window, escape);
  expect(screen.getByRole("complementary")).toBeTruthy();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("complementary")).toBeNull();
});
