import { DisclosureSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { SessionPolicyFields } from "./session-policy-fields";
import styles from "./credentials.module.css";

export function CapturedSessionPolicy({
  mount,
}: {
  mount: Schema["RunView"]["environment_mounts"][number];
}) {
  const { t } = useTranslation();
  return (
    <DisclosureSection title={t("Captured Session configuration")}>
      <p className={styles.note}>
        {t("Binding {{binding}} · Session version {{version}}", {
          binding: mount.session_binding ?? "default",
          version: mount.session_version || "—",
        })}
      </p>
      <p className={styles.note}>
        {t(
          "These references were captured when the Run was accepted. Current credential versions are shown for comparison and do not identify which token version each request used.",
        )}
      </p>
      <SessionPolicyFields value={mount.session_configuration ?? {}} readOnly />
    </DisclosureSection>
  );
}
