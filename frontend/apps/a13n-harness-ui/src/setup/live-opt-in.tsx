import { SettingsRow, Switch } from "a13n-ui";
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
    <SettingsRow
      label="Live voice"
      description="Use your Codex subscription. Start Live to turn on the microphone."
    >
      <Switch
        aria-label="Enable Live voice"
        checked={enabled}
        onCheckedChange={onChange}
      />
    </SettingsRow>
  );
}
