import { useState } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ModalFrame } from "a13n-ui";
import { expect, it, vi } from "vitest";
import { useMountedModal } from "./use-mounted-modal";
import { Confirm } from "./confirm";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

it("keeps a conditional editor mounted until the exit animation completes", async () => {
  let finish!: () => void;
  const finished = new Promise<Animation>((resolve) => {
    finish = () => resolve({} as Animation);
  });
  const animations = vi
    .spyOn(Element.prototype, "getAnimations")
    .mockReturnValue([{ finished, playState: "running" } as Animation]);
  const removed = vi.fn();
  function Editor({ onClosed }: { onClosed: () => void }) {
    const { modalProps } = useMountedModal(onClosed);
    return (
      <ModalFrame {...modalProps} title="Editor" closeLabel="Close">
        <p>Draft remains visible</p>
      </ModalFrame>
    );
  }
  function Owner() {
    const [editing, setEditing] = useState(true);
    return (
      editing && (
        <Editor
          onClosed={() => {
            removed();
            setEditing(false);
          }}
        />
      )
    );
  }
  try {
    render(<Owner />);
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.getByText("Draft remains visible")).toBeTruthy();
    expect(removed).not.toHaveBeenCalled();
    await act(async () => finish());
    await waitFor(() =>
      expect(screen.queryByText("Draft remains visible")).toBeNull(),
    );
    expect(removed).toHaveBeenCalledOnce();
  } finally {
    animations.mockRestore();
  }
});

it("dismisses a pending confirmation without restarting or canceling its work", async () => {
  let finish!: () => void;
  const action = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  const done = vi.fn();
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Confirm
        title="Delete item"
        description="Remove the selected item"
        subject="Example"
        trigger="Delete"
        action={action}
        onSuccess={done}
      />
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Delete" }));
  await user.click(screen.getByRole("button", { name: "Delete item" }));
  await waitFor(() => expect(action).toHaveBeenCalledOnce());
  await user.click(document.querySelector('[data-slot="dialog-viewport"]')!);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(done).not.toHaveBeenCalled();
  await act(async () => finish());
  await waitFor(() => expect(done).toHaveBeenCalledOnce());
  expect(action).toHaveBeenCalledOnce();
});
