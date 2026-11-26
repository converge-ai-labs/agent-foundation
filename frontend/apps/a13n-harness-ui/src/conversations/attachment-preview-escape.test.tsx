// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  AttachmentPreviewSheet,
  type AttachmentPreviewTarget,
} from "./attachment-preview";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";

beforeEach(() => {
  Object.defineProperty(Element.prototype, "getAnimations", {
    configurable: true,
    value: () => [],
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => ({
      left: 0,
      top: 0,
      right: 0,
      bottom: 0,
      width: 0,
      height: 0,
    }),
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("uses two-stage Escape with real SourceEditor FileSearch: first dismisses Find, second closes the drawer", async () => {
  const user = userEvent.setup();
  const content = "alpha line\nbeta needle line\ngamma line";
  const bytes = new TextEncoder().encode(content);
  const fetch = vi.fn().mockResolvedValue({
    blob: async () =>
      ({
        size: bytes.byteLength,
        type: "text/plain",
        arrayBuffer: async () =>
          bytes.buffer.slice(
            bytes.byteOffset,
            bytes.byteOffset + bytes.byteLength,
          ),
      }) as Blob,
  } as Response);

  const onClose = vi.fn();
  const target: AttachmentPreviewTarget = {
    kind: "retained",
    threadId: "thread-escape",
    attachmentId: "att-escape",
    name: "trace.txt",
    mediaType: "text/plain",
    size: bytes.byteLength,
  };

  render(
    <TransportContext value={{ fetch } as unknown as Transport}>
      <AttachmentPreviewSheet target={target} onClose={onClose} />
    </TransportContext>,
  );

  const dialog = await screen.findByRole("dialog", { name: "trace.txt" });
  await waitFor(() => expect(dialog.textContent).toContain("beta needle line"));

  // Open CodeMirror FileSearch via the Find button.
  await user.click(within(dialog).getByRole("button", { name: "Find" }));
  const searchRegion = await within(dialog).findByRole("search", {
    name: "Find",
  });
  expect(searchRegion).toBeTruthy();

  // First Escape closes Find without closing the drawer.
  await user.keyboard("{Escape}");
  await waitFor(() =>
    expect(within(dialog).queryByRole("search", { name: "Find" })).toBeNull(),
  );
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog", { name: "trace.txt" })).toBeTruthy();

  // Second Escape closes the drawer.
  await user.keyboard("{Escape}");
  await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
});
