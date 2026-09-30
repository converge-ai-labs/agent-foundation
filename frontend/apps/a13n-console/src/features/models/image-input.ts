import type { Schema } from "../../shared/api";

type Policy = Schema["ImageInputPolicy"];
const bytesPerMiB = 1024 * 1024;

export type ImageInputDraft = {
  enabled: boolean;
  supportGif: boolean;
  maxImages: string;
  maxSizeMiB: string;
  /** Retain policy fields not exposed by the simple editor. */
  policy: Policy | null | undefined;
};

export function imageInputDraft(policy?: Policy | null): ImageInputDraft {
  return {
    enabled: policy !== null,
    supportGif: policy?.support_gif ?? true,
    maxImages: String(policy?.max_images ?? 20),
    // Division by a power of two preserves exact integer byte budgets.
    maxSizeMiB: String(
      (policy?.max_image_bytes ?? 5 * bytesPerMiB) / bytesPerMiB,
    ),
    policy,
  };
}

export function imageInputErrors(draft: ImageInputDraft) {
  if (!draft.enabled) return {};
  const count = Number(draft.maxImages);
  const bytes = Number(draft.maxSizeMiB) * bytesPerMiB;
  return {
    maxImages:
      !draft.maxImages.trim() || !Number.isSafeInteger(count) || count < 0
        ? "Enter a whole number of images, zero or greater."
        : undefined,
    maxSizeMiB:
      !draft.maxSizeMiB.trim() || !Number.isSafeInteger(bytes) || bytes < 0
        ? "Enter a non-negative image size that converts to whole bytes."
        : undefined,
  };
}

export function imageInputPolicy(
  draft: ImageInputDraft,
): Policy | null | undefined {
  if (!draft.enabled) return null;
  const errors = imageInputErrors(draft);
  const error = errors.maxImages ?? errors.maxSizeMiB;
  if (error) throw new Error(error);
  const original = imageInputDraft(draft.policy);
  if (
    draft.policy !== null &&
    draft.supportGif === original.supportGif &&
    draft.maxImages === original.maxImages &&
    draft.maxSizeMiB === original.maxSizeMiB
  )
    return draft.policy;
  return {
    ...draft.policy,
    support_gif: draft.supportGif,
    max_images: Number(draft.maxImages),
    max_image_bytes: Number(draft.maxSizeMiB) * bytesPerMiB,
  };
}
