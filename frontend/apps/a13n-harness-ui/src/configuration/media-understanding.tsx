import { ChoiceField, SettingsRow } from "a13n-ui";
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
  const selectors = useSelectors();
  const document = readDocument(source);
  return (
    <>
      {mediaKinds.map((kind) => (
        <SettingsRow key={kind} label={mediaLabels[kind]}>
          <ChoiceField
            label={`${mediaLabels[kind]} understanding`}
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
                    ? "Environment"
                    : "Not configured",
              },
              ...(selectors.data?.models ?? [])
                .filter((model) => model.media_capabilities?.includes(kind))
                .map((model) => ({ value: model.model_id, label: model.name })),
            ]}
          />
        </SettingsRow>
      ))}
    </>
  );
}
