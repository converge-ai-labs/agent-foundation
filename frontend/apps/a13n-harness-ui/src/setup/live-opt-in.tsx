import { useTranslation } from "../i18n";
import { SettingsRow } from "../shell/settings";
import { Switch } from "a13n-ui";
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
  const { t } = useTranslation();
  if (model?.authentication.kind !== "codex_subscription") return null;
  return (
    <SettingsRow
      label={t("Live voice")}
      description={t(
        "Use your Codex subscription. Start Live to turn on the microphone.",
      )}
    >
      <Switch
        aria-label={t("Enable Live voice")}
        checked={enabled}
        onCheckedChange={onChange}
      />
    </SettingsRow>
  );
}
