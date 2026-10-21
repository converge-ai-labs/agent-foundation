import {
  ImageIcon,
  WaveformIcon,
  FilmStripIcon,
  FilePdfIcon,
  ArrowsOutSimpleIcon,
} from "@phosphor-icons/react";
import { Input, SettingsRow, SettingsSection, Switch } from "a13n-ui";
import styles from "./models.module.css";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";

type Characteristics = Schema["HarnessModelCharacteristics-Input"];
/** The inputs a Model can declare it reads, each with its mark. */
export const inputCapabilities = [
  { capability: "image_understanding", label: "Images", Icon: ImageIcon },
  { capability: "audio_understanding", label: "Audio", Icon: WaveformIcon },
  {
    capability: "video_understanding",
    label: "Video files",
    Icon: FilmStripIcon,
  },
  {
    capability: "document_understanding",
    label: "PDF documents",
    Icon: FilePdfIcon,
  },
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
      inputCapabilities.flatMap(({ capability }) =>
        capability === item ? [capability] : [],
      ),
    ),
  };
}

/** Seed new choices only. Saved declarations and manual PDF opt-in are untouched. */
export function characteristicsDefaults(
  value?: Schema["HarnessModelCharacteristics-Output"] | null,
): Characteristics {
  const defaults = characteristicsInput(value ?? undefined);
  return {
    ...defaults,
    capabilities: defaults.capabilities?.filter(
      (capability) => capability !== "document_understanding",
    ),
  };
}

/** Show declared facts, without making the reader re-enter catalog metadata. */
export function ModelCapabilitySummary({ value }: { value: Characteristics }) {
  const { t, i18n } = useTranslation();
  return (
    <div className={styles.capabilitySummary} aria-label={t("Capabilities")}>
      {inputCapabilities.map(({ capability, label, Icon }) => {
        if (!value.capabilities?.includes(capability)) return null;
        return (
          <span key={capability}>
            <Icon size={15} aria-hidden="true" />
            {t(label)}
          </span>
        );
      })}
      {value.context_window_tokens != null && (
        <span>
          <ArrowsOutSimpleIcon size={15} aria-hidden="true" />
          {t("{{tokens}} token context", {
            tokens: new Intl.NumberFormat(i18n.resolvedLanguage, {
              notation: "compact",
              maximumFractionDigits: 1,
            }).format(value.context_window_tokens),
          })}
        </span>
      )}
    </div>
  );
}

/** What agents may rely on: switches edit metadata, not provider capabilities. */
export function ModelInformation({
  value,
  onChange,
}: {
  value: Characteristics;
  onChange: (value: Characteristics) => void;
}) {
  const { t } = useTranslation();
  return (
    <SettingsSection title={t("Capabilities")} variant="plain">
      <div className={styles.capabilityGrid}>
        {inputCapabilities.map(({ capability, label }) => (
          <SettingsRow key={capability} label={t(label)} stackOnNarrow={false}>
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
        <SettingsRow label={t("YouTube URLs")} stackOnNarrow={false}>
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
      </div>
      <SettingsRow label={t("Context window")} description={t("Tokens")}>
        <Input
          className="w-40"
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
