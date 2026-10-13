/** Canvas/media are covered at the preparation owner and in real-browser review. */
import { beforeEach, vi } from "vitest";

const preparation = vi.hoisted(() => ({
  loadImage: vi.fn(),
  cropImage: vi.fn(),
}));
vi.mock("../src/shared/forms/image", async (original) => ({
  ...(await original<typeof import("../src/shared/forms/image")>()),
  ...preparation,
}));
vi.mock("react-easy-crop", async () => {
  const { useEffect } = await import("react");
  return {
    default: ({
      onCropAreaChange,
    }: {
      onCropAreaChange: (area: object, pixels: object) => void;
    }) => {
      useEffect(() => {
        onCropAreaChange(
          { x: 0, y: 0, width: 100, height: 100 },
          { x: 0, y: 0, width: 512, height: 512 },
        );
      }, []);
      return <div aria-label="Crop position" tabIndex={0} />;
    },
  };
});
export const preparedImage = new File(["prepared"], "avatar.webp", {
  type: "image/webp",
});
export const { cropImage, loadImage } = preparation;
beforeEach(() => {
  const image = { naturalWidth: 512, naturalHeight: 512 };
  preparation.loadImage.mockResolvedValue(image);
  preparation.cropImage.mockResolvedValue(preparedImage);
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:source");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
});
