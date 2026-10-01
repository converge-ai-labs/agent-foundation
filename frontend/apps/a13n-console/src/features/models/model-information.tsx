import { Input, SettingsRow, SettingsSection, Switch } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";

type Characteristics = Schema["HarnessModelCharacteristics-Input"];
const inputCapabilities = [
  ["image_understanding", "Images"],
  ["audio_understanding", "Audio"],
  ["video_understanding", "Video files"],
  ["document_understanding", "PDF documents"],
] as const;

/** Characteristics as read back, in the shape a model accepts: capabilities it knows only. */
export function characteristicsInput(
  value?: Schema["HarnessModelCharacteristics-Output"],
): Characteristics {
  return {
    ...value,
    url_input: value?.url_input
      ? {
          ...value.url_input,
          video: value.url_input.video?.flatMap((item) =>
            item === "youtube" ? ["youtube" as const] : [],
          ),
        }
      : undefined,
    capabilities: value?.capabilities?.flatMap((item) =>
      inputCapabilities.flatMap(([capability]) =>
        capability === item ? [capability] : [],
      ),
    ),
  };
}

/** What agents may rely on: one surface of switches and declared facts. */
export function ModelInformation({
  value,
  onChange,
}: {
  value: Characteristics;
  onChange: (value: Characteristics) => void;
}) {
  const { t } = useTranslation();
  return (
    <SettingsSection
      title={t("Capabilities")}
      description={t("What agents can rely on when they run this model.")}
    >
      {inputCapabilities.map(([capability, label]) => (
        <SettingsRow key={capability} label={t(label)}>
          <Switch
            aria-label={t(label)}
            checked={value.capabilities?.includes(capability) ?? false}
            onCheckedChange={(enabled) =>
              onChange({
                ...value,
                capabilities: enabled
                  ? [...(value.capabilities ?? []), capability]
                  : value.capabilities?.filter((item) => item !== capability),
              })
            }
          />
        </SettingsRow>
      ))}
      <SettingsRow label={t("YouTube URLs")}>
        <Switch
          aria-label={t("YouTube URLs")}
          checked={value.url_input?.video?.includes("youtube") ?? false}
          onCheckedChange={(enabled) =>
            onChange({
              ...value,
              url_input: {
                ...value.url_input,
                video: enabled ? ["youtube"] : [],
              },
            })
          }
        />
      </SettingsRow>
      <SettingsRow label={t("Context window")} description={t("Tokens")}>
        <Input
          type="number"
          min={1}
          step={1}
          aria-label={t("Context window")}
          placeholder={t("Unknown")}
          value={value.context_window_tokens ?? ""}
          onChange={(event) =>
            onChange({
              ...value,
              context_window_tokens:
                event.target.value === "" ? null : Number(event.target.value),
            })
          }
        />
      </SettingsRow>
    </SettingsSection>
  );
}
