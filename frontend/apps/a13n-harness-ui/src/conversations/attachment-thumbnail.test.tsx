// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { retainedImage } from "./attachment-thumbnail";
import { InputContent } from "./input-content";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";

const attachment: Schema<"ThreadAttachment"> = {
  attachment_id: "attachment-image",
  name: "photo.png",
  media_type: "image/png",
  size: 3,
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function urls() {
  const createObjectURL = vi.fn().mockReturnValue("blob:retained-image");
  const revokeObjectURL = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    },
  );
  return { createObjectURL, revokeObjectURL };
}
it("loads retained image bytes through transport and revokes its URL on disposal", async () => {
  const url = urls();
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response("png", { headers: { "Content-Type": "image/png" } }),
    );
  const image = document.createElement("img");
  const dispose = retainedImage(
    image,
    { fetch } as unknown as Transport,
    "thread one",
    attachment,
  );
  expect(fetch.mock.calls[0][0]).toBe(
    "/api/threads/thread%20one/attachments/attachment-image",
  );
  await waitFor(() => expect(image.src).toBe("blob:retained-image"));
  fireEvent.load(image);
  expect(image.hidden).toBe(false);
  fireEvent.error(image);
  expect(image.hidden).toBe(true);
  dispose();
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
  expect(url.revokeObjectURL).toHaveBeenCalledWith("blob:retained-image");
});
it("does not create a late object URL after cancellation or fetch non-image attachments", async () => {
  const url = urls();
  let resolve!: (response: Response) => void;
  const pending = new Promise<Response>((done) => {
    resolve = done;
  });
  const fetch = vi.fn().mockReturnValue(pending);
  const image = document.createElement("img");
  const dispose = retainedImage(
    image,
    { fetch } as unknown as Transport,
    "one",
    attachment,
  );
  dispose();
  resolve(new Response("late"));
  await pending;
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(url.createObjectURL).not.toHaveBeenCalled();
  const noImage = retainedImage(
    image,
    { fetch } as unknown as Transport,
    "one",
    { ...attachment, media_type: "text/plain" },
  );
  expect(fetch).toHaveBeenCalledTimes(1);
  noImage();
});
it("renders one thumbnail per authored occurrence and keeps hidden descriptions out of ordered echo", async () => {
  urls();
  const fetch = vi.fn().mockResolvedValue(new Response("png"));
  const metadata = (index: number) => ({
    source_id: "input-one",
    harness_ui: { composer: { index, label: "image#1" }, attachment },
  });
  const view = render(
    <TransportContext value={{ fetch } as unknown as Transport}>
      <InputContent
        threadId="one"
        renderText={(text) => text}
        parts={[
          {
            kind: "user",
            text: "before ",
            metadata: {
              source_id: "input-one",
              harness_ui: { composer: { index: 0 } },
            },
          },
          {
            kind: "user",
            text: "hidden description",
            metadata: { ...metadata(1), display: false },
          },
          { kind: "media", value: { kind: "binary" }, metadata: metadata(1) },
          {
            kind: "user",
            text: " between ",
            metadata: {
              source_id: "input-one",
              harness_ui: { composer: { index: 2 } },
            },
          },
          { kind: "media", value: { kind: "binary" }, metadata: metadata(3) },
        ]}
      />
    </TransportContext>,
  );
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(screen.getAllByText("photo.png")).toHaveLength(2);
  expect(screen.queryByText("hidden description")).toBeNull();
  const visible = [
    ...view.container.querySelectorAll("header ~ span, header ~ div > button"),
  ].map((node) => node.textContent);
  expect(visible).toEqual(["before ", "photo.png", " between ", "photo.png"]);
});

it("opens retained images in a full-screen dialog with fit, actual-size and download controls", async () => {
  const url = urls();
  const fetch = vi.fn().mockImplementation(async () => new Response("png"));
  render(
    <TransportContext value={{ fetch } as unknown as Transport}>
      <InputContent
        threadId="one"
        renderText={(text) => text}
        parts={[{ kind: "media", metadata: { harness_ui: { attachment } } }]}
      />
    </TransportContext>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Preview photo.png" }));
  const dialog = await screen.findByRole("dialog", { name: "photo.png" });
  await waitFor(() =>
    expect(dialog.querySelector("img")?.getAttribute("src")).toBe(
      "blob:retained-image",
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Actual size" }));
  expect(screen.getByRole("button", { name: "Fit to screen" })).toBeTruthy();
  expect(
    screen
      .getByRole("link", { name: "Download image" })
      .getAttribute("download"),
  ).toBe("photo.png");
  fireEvent.click(screen.getByRole("button", { name: "Close image preview" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(url.revokeObjectURL).toHaveBeenCalledWith("blob:retained-image");
});
