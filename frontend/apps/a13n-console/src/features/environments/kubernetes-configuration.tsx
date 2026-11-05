import { Button } from "a13n-ui";
import { ArrowSquareOutIcon } from "@phosphor-icons/react";
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
      {!readOnly && (
        <div className={styles.stack}>
          <p className={styles.muted}>
            {t(
              "Before the first run, load the sandbox image into your cluster or use a registry image the nodes can pull. Both connection modes require the operator's allow_plaintext_envd opt-in.",
            )}
          </p>
          <a
            href="https://a13n.dev/docs/a13n-service/environments#kubernetes-sandboxes"
            target="_blank"
            rel="noreferrer"
            className={styles.actions}
          >
            {t("Kubernetes setup guide")}
            <ArrowSquareOutIcon size={14} />
          </a>
        </div>
      )}
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
          "Starters use a13n-sandbox:local. Build with make image-sandbox, then load it with kind load docker-image a13n-sandbox:local --name <cluster>. Use env/valueFrom for container variables, environment for execution variables, and storage for PVC bindings.",
        )}
      />
      {children}
    </FormSection>
  );
}
