// @vitest-environment node
import { expect, it } from "vitest";
import { imageInputDraft, imageInputPolicy } from "./image-input";

it("uses default-on preparation and preserves omission or explicit null", () => {
  expect(imageInputDraft()).toMatchObject({
    enabled: true,
    supportGif: true,
    maxImages: "20",
    maxSizeMiB: "5",
  });
  expect(imageInputPolicy(imageInputDraft())).toBeUndefined();
  expect(imageInputDraft(null).enabled).toBe(false);
  expect(imageInputPolicy(imageInputDraft(null))).toBeNull();
  expect(imageInputPolicy({ ...imageInputDraft(null), enabled: true })).toEqual(
    { support_gif: true, max_images: 20, max_image_bytes: 5242880 },
  );
});

it.each([0, 1, 1234567, 5242881, 8000000, Number.MAX_SAFE_INTEGER])(
  "retains an exact byte budget of %s through load and unrelated edits",
  (bytes) => {
    const policy = {
      max_image_bytes: bytes,
      max_image_dimension: 4321,
      split_large_images: false,
      image_split_max_height: 777,
      image_split_overlap: 12,
    };
    const draft = imageInputDraft(policy);
    expect(imageInputPolicy(draft)).toEqual(policy);
    expect(imageInputPolicy({ ...draft, supportGif: false })).toEqual({
      ...policy,
      support_gif: false,
      max_images: 20,
    });
  },
);

it("converts MiB without rounding and keeps hidden policy members", () => {
  expect(
    imageInputPolicy({
      ...imageInputDraft({ image_split_overlap: 99 }),
      maxSizeMiB: "2.5",
      maxImages: "0",
    }),
  ).toEqual({
    image_split_overlap: 99,
    support_gif: true,
    max_images: 0,
    max_image_bytes: 2621440,
  });
});

it.each(["", " ", "-1", "1.5", "NaN", "Infinity", "9007199254740992"])(
  "rejects invalid image counts: %s",
  (value) => {
    expect(() =>
      imageInputPolicy({ ...imageInputDraft(), maxImages: value }),
    ).toThrow("whole number");
  },
);

it.each(["", " ", "-1", "NaN", "Infinity", "0.0000001", "9007199254740992"])(
  "rejects invalid image sizes: %s",
  (value) => {
    expect(() =>
      imageInputPolicy({ ...imageInputDraft(), maxSizeMiB: value }),
    ).toThrow("whole bytes");
  },
);

it("disabled preparation ignores inactive invalid fields and toggling retains hidden values", () => {
  const draft = imageInputDraft({ split_large_images: false });
  expect(
    imageInputPolicy({ ...draft, enabled: false, maxImages: "" }),
  ).toBeNull();
  expect(imageInputPolicy({ ...draft, enabled: true })).toEqual({
    split_large_images: false,
  });
});
