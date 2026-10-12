import type { Schema } from "../transport/client";

export function LiveOptIn({
  model,
  enabled,
  onChange,
}: {
  model: Schema<"ModelRecipe"> | null | undefined;
  enabled: boolean;
  onChange: (enabled: boolean) => void;
}) {
  if (model?.authentication.kind !== "codex_subscription") return null;
  return (
    <div>
      <label>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(event) => onChange(event.target.checked)}
        />{" "}
        Enable Live voice with this Codex Model
      </label>
      <p>
        Uses your Codex subscription for voice and task execution. Your
        microphone stays off until you start Live. Existing Live settings remain
        unchanged.
      </p>
    </div>
  );
}
