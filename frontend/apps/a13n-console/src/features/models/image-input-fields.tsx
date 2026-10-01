import {
  FormField,
  Input,
  SettingsRow,
  SettingsSection,
  Switch,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import sharedStyles from "../../shared/shared.module.css";
import { imageInputErrors, type ImageInputDraft } from "./image-input";

export function ImageInputFields({
  value,
  onChange,
}: {
  value: ImageInputDraft;
  onChange: (value: ImageInputDraft) => void;
}) {
  const { t } = useTranslation();
  const errors = imageInputErrors(value);
  return (
    <SettingsSection title={t("Image input")}>
      <SettingsRow
        label={t("Prepare images")}
        description={t(
          "Validate and resize images before sending them to this model.",
        )}
      >
        <Switch
          aria-label={t("Prepare images")}
          checked={value.enabled}
          onCheckedChange={(enabled) => onChange({ ...value, enabled })}
        />
      </SettingsRow>
      {value.enabled && (
        <>
          <SettingsRow
            label={t("Support GIF")}
            description={t(
              "Keep binary GIF images; when off, they are omitted from requests.",
            )}
          >
            <Switch
              aria-label={t("Support GIF")}
              checked={value.supportGif}
              onCheckedChange={(supportGif) =>
                onChange({ ...value, supportGif })
              }
            />
          </SettingsRow>
          <div className={sharedStyles.twoColumns}>
            <FormField
              label={t("Max images")}
              description={
                errors.maxImages
                  ? undefined
                  : t("Per request. Zero removes all images.")
              }
              error={errors.maxImages && t(errors.maxImages)}
            >
              <Input
                type="number"
                min={0}
                step={1}
                required
                value={value.maxImages}
                onChange={(event) =>
                  onChange({ ...value, maxImages: event.target.value })
                }
              />
            </FormField>
            <FormField
              label={t("Max image size (MiB)")}
              description={
                errors.maxSizeMiB
                  ? undefined
                  : t(
                      "Base64-encoded size per image. 1 MiB = 1,048,576 bytes; zero disables the limit.",
                    )
              }
              error={errors.maxSizeMiB && t(errors.maxSizeMiB)}
            >
              <Input
                type="number"
                min={0}
                step="any"
                required
                value={value.maxSizeMiB}
                onChange={(event) =>
                  onChange({ ...value, maxSizeMiB: event.target.value })
                }
              />
            </FormField>
          </div>
        </>
      )}
    </SettingsSection>
  );
}
