import { useTranslation } from "react-i18next";
import { ModelIcon } from "../shell/model-identity";
import { SettingsRow, SettingsSection } from "../shell/settings";
import { ChoiceField } from "a13n-ui";
import { useSelectors } from "../transport/context";
import { readDocument, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";

export const mediaKinds = ["image", "video", "audio"] as const;
export const mediaLabels = { image: "Image", video: "Video", audio: "Audio" };

export function MediaUnderstandingFields({
  source,
  onChange,
}: {
  source: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const selectors = useSelectors();
  const labels = { image: t("Image"), video: t("Video"), audio: t("Audio") };
  const understandingLabels = {
    image: t("Image understanding"),
    video: t("Video understanding"),
    audio: t("Audio understanding"),
  };
  const document = readDocument(source);
  return (
    <SettingsSection variant="grouped">
      {mediaKinds.map((kind) => (
        <SettingsRow key={kind} label={labels[kind]}>
          <ChoiceField
            label={understandingLabels[kind]}
            hideLabel
            className={styles.settingControl}
            value={String(document?.getIn(["media_understanding", kind]) ?? "")}
            onValueChange={(value) =>
              onChange(
                updateDocument(
                  source,
                  ["media_understanding", kind],
                  value || null,
                ),
              )
            }
            options={[
              {
                value: "",
                label:
                  selectors.data?.media_understanding_environment?.includes(
                    kind,
                  )
                    ? t("Environment")
                    : t("Not configured"),
              },
              ...(selectors.data?.models ?? [])
                .filter((model) => model.media_capabilities?.includes(kind))
                .map((model) => ({
                  value: model.model_id,
                  label: model.name,
                  icon: <ModelIcon model={model} />,
                })),
            ]}
          />
        </SettingsRow>
      ))}
    </SettingsSection>
  );
}
