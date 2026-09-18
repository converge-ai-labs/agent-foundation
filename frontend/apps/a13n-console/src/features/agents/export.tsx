import { DownloadSimpleIcon } from "@phosphor-icons/react";
import {
  Button,
  ModalFrame,
  Tabs,
  TabsList,
  TabsPanel,
  TabsTab,
} from "a13n-ui";
import { useState, type ReactElement } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { CopyButton } from "../../shared/identity";
import { downloadBlob } from "../../shared/download";
import { ErrorToast } from "../../shared/feedback";
import { agentFile, serializeAgentFile, type AgentFile } from "./transfer";
import type { AgentConfig } from "./configuration";
import styles from "./agents.module.css";

export function AgentFilePreview({ file }: { file: AgentFile }) {
  const { t } = useTranslation();
  const yaml = serializeAgentFile(file);
  return (
    <Tabs defaultValue="rendered" className={styles.preview}>
      <div className={styles.previewToolbar}>
        <TabsList size="sm" aria-label={t("Agent config")}>
          <TabsTab value="rendered">{t("Rendered")}</TabsTab>
          <TabsTab value="raw">{t("Raw")}</TabsTab>
        </TabsList>
        <CopyButton value={yaml} copyLabel={t("Copy YAML")} />
      </div>
      <TabsPanel value="rendered" className={styles.previewBody}>
        <div className={styles.previewIdentity}>
          <h3>{file.name}</h3>
          {file.description && <p>{file.description}</p>}
        </div>
        <dl className={styles.previewFacts}>
          <div>
            <dt>{t("Model")}</dt>
            <dd>{file.config.model.model_key}</dd>
          </div>
          <div>
            <dt>{t("Capabilities")}</dt>
            <dd>
              {t("{{skills}} skills · {{connections}} connections", {
                skills: file.config.skills?.length ?? 0,
                connections: file.config.connection_tools?.length ?? 0,
              })}
            </dd>
          </div>
        </dl>
        <div className={styles.previewInstructions}>
          <h4>{t("Instructions")}</h4>
          <p className="a13n-scrollbar">{file.config.instructions || "—"}</p>
        </div>
        <p className={styles.previewNote}>
          {t(
            "Raw YAML includes the complete configuration and dependency references.",
          )}
        </p>
      </TabsPanel>
      <TabsPanel value="raw" className="min-w-0">
        <pre
          aria-label={t("Agent YAML")}
          className={`${styles.rawYaml} a13n-scrollbar`}
        >
          <code>{yaml}</code>
        </pre>
      </TabsPanel>
    </Tabs>
  );
}

export function ExportAgent({
  agent,
  config,
  version,
  trigger,
}: {
  agent: Schema["Agent"];
  config: AgentConfig;
  version: number;
  trigger?: ReactElement;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<Error>();
  const file = agentFile(agent, config);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      trigger={
        trigger ?? (
          <Button variant="ghost">
            <DownloadSimpleIcon size={14} />
            {t("Export agent")}
          </Button>
        )
      }
      title={t("Export agent")}
      description={t(
        "Export the saved version as YAML to back up or import into another workspace.",
      )}
      closeLabel={t("Close")}
      size="lg"
      footer={
        <div className={styles.exportActions}>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            {t("Close")}
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              try {
                downloadBlob(
                  new Blob([serializeAgentFile(file)], {
                    type: "application/yaml;charset=utf-8",
                  }),
                  `${agent.key}.yaml`,
                );
                setError(undefined);
              } catch {
                setError(new Error(t("Agent download failed. Try again.")));
              }
            }}
          >
            <DownloadSimpleIcon size={15} />
            {t("Download YAML")}
          </Button>
        </div>
      }
    >
      <div className={styles.exportBody}>
        <p className={styles.previewNote}>
          {t(
            "Saved version {{version}} · Credentials and dependent resources are not included.",
            { version },
          )}
        </p>
        <AgentFilePreview file={file} />
        <ErrorToast error={error} />
      </div>
    </ModalFrame>
  );
}
