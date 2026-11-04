import { Button } from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { FormSection, jsonObject, TextAreaField } from "../../shared/forms";
import styles from "../../shared/shared.module.css";

/** Native Pod authoring stays native; starters fill an empty draft, never merge over user manifests. */
export function KubernetesConfiguration({
  schema,
  text,
  onChange,
  readOnly,
  error,
  children,
}: {
  schema?: Record<string, unknown>;
  text: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
  error?: string;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  let empty = false;
  try {
    empty = Object.keys(jsonObject(text)).length === 0;
  } catch {
    /* Preserve unfinished JSON. */
  }
  const presets = schema?.["x-environment-presets"] as
    Record<string, unknown> | undefined;
  return (
    <FormSection
      title={t("Kubernetes recipe")}
      description={t(
        "Author a native PodTemplateSpec with an explicit Envd container. Names, credentials and sandbox identity are supplied by the Service.",
      )}
    >
      {empty && presets && !readOnly && (
        <div className={styles.actions}>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onChange(JSON.stringify(presets.direct, null, 2))}
          >
            {t("In-cluster starter")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() =>
              onChange(JSON.stringify(presets.port_forward, null, 2))
            }
          >
            {t("Local port-forward starter")}
          </Button>
        </div>
      )}
      <TextAreaField
        label={t("Native recipe JSON")}
        value={text}
        onChange={onChange}
        readOnly={readOnly}
        error={error}
        code
        rows={18}
        hint={t(
          "Set an Envd image available to your cluster. Starters use a13n-sandbox:local from make image-sandbox. Use native env/valueFrom for container variables, environment for execution variables, and storage for PVC bindings.",
        )}
      />
      {children}
    </FormSection>
  );
}
