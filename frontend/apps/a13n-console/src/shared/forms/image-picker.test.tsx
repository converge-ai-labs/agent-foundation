import { cropImage, loadImage, preparedImage } from "../../../tests/image-crop";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ImagePicker } from "./image-picker";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function setup(onChange = vi.fn()) {
  const view = render(
    <ImagePicker hasImage editable pending={false} onChange={onChange}>
      <span>Current image</span>
    </ImagePicker>,
  );
  const input =
    view.container.querySelector<HTMLInputElement>('input[type="file"]')!;
  return { ...view, input, onChange, user: userEvent.setup() };
}
const source = new File(["original"], "portrait.png", { type: "image/png" });

it("keeps the existing image until confirmation, cancels cleanly, and releases the source URL", async () => {
  const { input, onChange, user } = setup();
  const trigger = screen.getAllByRole("button", { name: "Upload image" })[0]!;
  trigger.focus();
  await user.upload(input, source);
  await screen.findByRole("dialog", { name: "Crop image" });
  expect(onChange).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(cropImage).not.toHaveBeenCalled();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:source");
  expect(screen.getByText("Current image")).toBeTruthy();
});

it("uploads only the prepared file and allows retry after an upload failure", async () => {
  const change = vi
    .fn()
    .mockRejectedValueOnce(new Error("Upload interrupted"))
    .mockResolvedValue(undefined);
  const { input, user } = setup(change);
  await user.upload(input, source);
  const save = await screen.findByRole("button", { name: "Use image" });
  await user.click(save);
  await screen.findByText("Upload interrupted");
  expect(change).toHaveBeenCalledWith(preparedImage);
  expect(screen.getByRole("dialog", { name: "Crop image" })).toBeTruthy();
  await user.click(save);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(change).toHaveBeenCalledTimes(2);
});

it("shows preparation errors without submitting and preserves the crop for correction", async () => {
  cropImage.mockRejectedValue(
    new Error("This image could not be prepared. Try another image."),
  );
  const { input, onChange, user } = setup();
  await user.upload(input, source);
  await user.click(await screen.findByRole("button", { name: "Use image" }));
  await screen.findByText(
    "This image could not be prepared. Try another image.",
  );
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
});

it("rejects oversized sources before loading and reports corrupt media inside the cancellable crop", async () => {
  const { input, onChange, user } = setup();
  const large = new File(["large"], "large.png", { type: "image/png" });
  Object.defineProperty(large, "size", { value: 20 * 1024 * 1024 + 1 });
  await user.upload(input, large);
  await screen.findByText("Choose an image smaller than 20 MiB.");
  expect(loadImage).not.toHaveBeenCalled();
  loadImage.mockRejectedValue(
    new Error("This image could not be opened. Choose another image."),
  );
  await user.upload(input, source);
  await screen.findByText(
    "This image could not be opened. Choose another image.",
  );
  expect(onChange).not.toHaveBeenCalled();
  expect(
    (screen.getByRole("button", { name: "Use image" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});

it("allows dismissal while image preparation finishes without duplicate confirmation", async () => {
  let finish!: (file: File) => void;
  cropImage.mockReturnValue(
    new Promise<File>((resolve) => {
      finish = resolve;
    }),
  );
  const { input, onChange, user } = setup();
  await user.upload(input, source);
  const save = await screen.findByRole("button", { name: "Use image" });
  await user.click(save);
  await screen.findByRole("status");
  expect(
    (screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(save.hasAttribute("disabled")).toBe(true);
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(cropImage).toHaveBeenCalledOnce();
  finish(preparedImage);
  await waitFor(() => expect(onChange).toHaveBeenCalledOnce());
});
