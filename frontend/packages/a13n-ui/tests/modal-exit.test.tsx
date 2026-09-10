import { render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Button, ModalFrame } from "../src";

it("retains content when the caller clears it during exit and uses fresh content on reopen", async () => {
  const finished = new Promise<Animation>(() => {});
  const animations = vi
    .spyOn(Element.prototype, "getAnimations")
    .mockReturnValue([{ finished, playState: "running" } as Animation]);
  const view = (open: boolean, content: string) => (
    <ModalFrame
      open={open}
      title="Provider"
      closeLabel="Close"
      trigger={<Button>Open</Button>}
    >
      {open && <p>{content}</p>}
    </ModalFrame>
  );
  try {
    const { rerender } = render(view(true, "Provider configuration"));
    await screen.findByText("Provider configuration");
    rerender(view(false, ""));
    expect(screen.getByText("Provider configuration")).toBeTruthy();
    rerender(view(true, "New configuration"));
    await waitFor(() =>
      expect(screen.queryByText("Provider configuration")).toBeNull(),
    );
    expect(screen.getByText("New configuration")).toBeTruthy();
  } finally {
    animations.mockRestore();
  }
});
