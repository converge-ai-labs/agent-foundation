import { DownloadSimpleIcon } from "@phosphor-icons/react";
import {
  Button,
  ModalFrame,
  Tabs,
  TabsList,
  TabsPanel,
  TabsTab,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { CopyButton } from "../../shared/copy";
import { downloadBlob } from "../../shared/download";
import { ErrorToast } from "../../shared/feedback";
import { agentFile, serializeAgentFile, type AgentFile } from "./transfer";
import type { AgentConfig } from "./configuration";

export function AgentFilePreview({ file }: { file: AgentFile }) {
  const { t } = useTranslation();
  const yaml = serializeAgentFile(file);
  return (
    <Tabs defaultValue="rendered" className="min-w-0">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <TabsList aria-label={t("Agent config")}>
          <TabsTab value="rendered">{t("Rendered")}</TabsTab>
          <TabsTab value="raw">{t("Raw")}</TabsTab>
        </TabsList>
        <CopyButton value={yaml} copyLabel={t("Copy YAML")} />
      </div>
      <TabsPanel value="rendered" className="space-y-5">
        <div>
          <h3 className="break-words text-base font-semibold">{file.name}</h3>
          {file.description && (
            <p className="mt-1 whitespace-pre-wrap break-words text-sm text-muted-foreground">
              {file.description}
            </p>
          )}
        </div>
        <dl className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <dt className="text-muted-foreground">{t("Model")}</dt>
            <dd className="mt-1 break-all">{file.config.model.model_key}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">{t("Capabilities")}</dt>
            <dd className="mt-1">
              {t("{{skills}} skills · {{connections}} connections", {
                skills: file.config.skills?.length ?? 0,
                connections: file.config.connection_tools?.length ?? 0,
              })}
            </dd>
          </div>
        </dl>
        <div>
          <h4 className="mb-2 text-sm text-muted-foreground">
            {t("Instructions")}
          </h4>
          <p className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-sm a13n-scrollbar">
            {file.config.instructions || "—"}
          </p>
        </div>
        <p className="text-xs text-muted-foreground">
          {t(
            "Raw YAML includes the complete configuration and dependency references.",
          )}
        </p>
      </TabsPanel>
      <TabsPanel value="raw" className="min-w-0">
        <pre
          aria-label={t("Agent YAML")}
          className="max-h-96 overflow-auto rounded-lg border bg-muted/30 p-4 text-xs leading-6 a13n-scrollbar"
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
}: {
  agent: Schema["Agent"];
  config: AgentConfig;
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
        <Button variant="ghost">
          <DownloadSimpleIcon size={14} />
          {t("Export agent")}
        </Button>
      }
      title={t("Export agent")}
      description={t(
        "Export the saved version as YAML to back up or import into another workspace.",
      )}
      closeLabel={t("Close")}
      size="lg"
      footer={
        <div className="flex justify-end gap-3">
          <Button variant="ghost" onClick={() => setOpen(false)}>
            {t("Close")}
          </Button>
          <Button
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
      <div className="space-y-5">
        <p className="text-xs text-muted-foreground">
          {t(
            "Saved version {{version}} · Credentials and dependent resources are not included.",
            { version: agent.version },
          )}
        </p>
        <AgentFilePreview file={file} />
        <ErrorToast error={error} />
      </div>
    </ModalFrame>
  );
}
