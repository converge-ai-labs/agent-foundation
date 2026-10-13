import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useWorkspace } from "../../layout/workspace";
import { Page } from "../../shared/page";
import { TemplateConfig } from "./template-config";
import styles from "./environments.module.css";

export function CreateEnvironmentTemplate() {
  const { t } = useTranslation(),
    { basePath, can } = useWorkspace(),
    navigate = useNavigate();
  return (
    <Page
      title={t("Create environment template")}
      description={t(
        "Choose a runtime, connect your infrastructure, and save settings for future environments.",
      )}
      back={`${basePath}/environments`}
      backLabel={t("Environment templates")}
      className={styles.creationPage}
    >
      {can("write") ? (
        <TemplateConfig
          standalone
          close={() => navigate(`${basePath}/environments`)}
        />
      ) : (
        <p>{t("You need write access to create an environment template.")}</p>
      )}
    </Page>
  );
}
