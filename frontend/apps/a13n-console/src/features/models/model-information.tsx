import {
  ChoiceField,
  Input,
  SettingsRow,
  SettingsSection,
  Switch,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";

const inputCapabilities = [
  ["image_understanding", "Images"],
  ["audio_understanding", "Audio"],
  ["video_understanding", "Video"],
] as const;

/** What agents may rely on: one surface of switches and declared facts. */
export function ModelInformation({
  value,
  onChange,
}: {
  value: Schema["ModelDeclarations-Input"];
  onChange: (value: Schema["ModelDeclarations-Input"]) => void;
}) {
  const { t } = useTranslation();
  const options = [
    { value: "unknown", label: t("Unknown") },
    { value: "true", label: t("Supported") },
    { value: "false", label: t("Unsupported") },
  ];
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
      <SettingsRow label={t("Tool calling support")}>
        <ChoiceField
          label={t("Tool calling support")}
          hideLabel
          options={options}
          value={String(value.supports_tools ?? "unknown")}
          onValueChange={(next) =>
            onChange({
              ...value,
              supports_tools: next === "unknown" ? null : next === "true",
            })
          }
        />
      </SettingsRow>
      <SettingsRow label={t("Structured output")}>
        <ChoiceField
          label={t("Structured output")}
          hideLabel
          options={options}
          value={String(value.structured_output ?? "unknown")}
          onValueChange={(next) =>
            onChange({
              ...value,
              structured_output: next === "unknown" ? null : next === "true",
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
