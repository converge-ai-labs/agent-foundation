import { BrainIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  FormField,
  Input,
  SearchPicker,
  SegmentedControl,
} from "a13n-ui";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { Empty } from "../../shared/collection";
import { ErrorNotice } from "../../shared/feedback";
import { Page } from "../../shared/page";
import { ManageProvidersLink } from "../providers/manage-link";
import { memoryKey, type MemoryTarget } from "./api";
import { FileMemoryBrowser } from "./documents";
import { MemoryContents } from "./contents";
import {
  eligibleMemoryProvider,
  useMemoryProviders,
  useWorkspaceMemoryProviderDefinitions,
} from "./availability";
import styles from "./memory.module.css";

export function MemoriesPage() {
  const [params, setParams] = useSearchParams();
  const { t } = useTranslation();
  const [mode, setMode] = useState("records");
  const modes = (
    <SegmentedControl
      label={t("Memory view")}
      value={mode}
      onValueChange={setMode}
      options={[
        { value: "records", label: t("Records") },
        { value: "files", label: t("Files") },
      ]}
    />
  );
  return mode === "files" ? (
    <MemoryFilesPage modes={modes} />
  ) : (
    <MemoryPageSelection
      key={params.toString()}
      modes={modes}
      params={params}
      onSelect={setParams}
    />
  );
}

/** Title, description, and the view switch every memory view shares. */
function MemoriesFrame({
  description,
  modes,
  toolbar,
  children,
}: {
  description: string;
  modes: ReactNode;
  toolbar?: ReactNode;
  children: ReactNode;
}) {
  const { t } = useTranslation(),
    { can } = useWorkspace();
  return (
    <Page
      title={t("Memories")}
      description={description}
      actions={
        can("memory_provider.read") ? (
          <ManageProvidersLink category="memory" scope="workspace" />
        ) : undefined
      }
      toolbar={
        <div className={styles.views}>
          {modes}
          {toolbar}
        </div>
      }
    >
      {children}
    </Page>
  );
}

function MemoryFilesPage({ modes }: { modes: ReactNode }) {
  const { t } = useTranslation();
  return (
    <MemoriesFrame
      modes={modes}
      description={t(
        "Read and edit the documents an agent keeps in a file memory store.",
      )}
    >
      <FileMemoryBrowser />
    </MemoriesFrame>
  );
}

function MemoryPageSelection({
  modes,
  params,
  onSelect,
}: {
  modes: ReactNode;
  params: URLSearchParams;
  onSelect: (params: URLSearchParams) => void;
}) {
  const { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    client = useClient();
  const rawScope = params.get("scope");
  const initialScope =
    rawScope === "thread" || rawScope === "agent" ? rawScope : "user";
  const [provider, setProvider] = useState(params.get("provider") ?? "");
  const [scope, setScope] = useState<MemoryTarget["scope"]>(initialScope);
  const [subject, setSubject] = useState(params.get("subject") ?? "");
  const [editing, setEditing] = useState(false);
  const { providers } = useMemoryProviders();
  const definitions = useWorkspaceMemoryProviderDefinitions();
  const eligible = (provider: Schema["MemoryProvider"]) =>
    eligibleMemoryProvider(provider, definitions.data?.items ?? []);
  const agents = useQuery({
    queryKey: ["agents", workspace.id, "memory-subjects"],
    enabled: scope === "agent",
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace}/agents", {
            params: {
              path: { workspace: workspace.id },
              query: { limit: 100, cursor, include_archived: true },
            },
            signal,
          })
          .then(data),
      ),
  });
  const target: MemoryTarget | null =
    params.get("provider") && (initialScope === "user" || params.get("subject"))
      ? {
          workspace: workspace.id,
          provider_id: params.get("provider")!,
          scope: initialScope,
          subject_id:
            initialScope === "user" ? undefined : params.get("subject")!,
        }
      : null;
  const selected = providers.data?.find((item) => item.id === provider);
  const incomplete = !provider.trim() || (scope !== "user" && !subject.trim());
  const scopeLabels = {
    user: t("My memories"),
    agent: t("Agent"),
    thread: t("Thread"),
  };
  return (
    <MemoriesFrame
      modes={modes}
      description={t(
        "Inspect and manage explicit memories for one provider and subject. Nothing is combined across scopes.",
      )}
      toolbar={
        <form
          className={styles.selector}
          onSubmit={(event) => {
            event.preventDefault();
            if (editing || incomplete) return;
            const next = new URLSearchParams({
              provider: provider.trim(),
              scope,
            });
            if (scope !== "user") next.set("subject", subject.trim());
            onSelect(next);
          }}
        >
          <fieldset disabled={editing} className={styles.selectorFields}>
            {can("memory_provider.read") ? (
              <div className={styles.selectorField}>
                <SearchPicker
                  label={t("Memory provider")}
                  placeholder={t("Provider")}
                  emptyMessage={t("No memory providers available.")}
                  value={provider}
                  onValueChange={(value) => setProvider(value ?? "")}
                  groups={[
                    {
                      label: t("Providers"),
                      options: [
                        ...(providers.data ?? []).map((item) => ({
                          value: item.id,
                          label: item.name,
                          description: `${t(item.workspace_id ? "Workspace" : "Organization")}${eligible(item) ? "" : ` · ${t("Unavailable")}`}`,
                        })),
                        ...(provider && !selected
                          ? [
                              {
                                value: provider,
                                label: provider,
                                description: t("Selected provider unavailable"),
                              },
                            ]
                          : []),
                      ],
                    },
                  ]}
                />
              </div>
            ) : (
              <FormField
                hideLabel
                className={styles.selectorField}
                label={t("Memory provider ID")}
              >
                <Input
                  required
                  placeholder={t("Memory provider ID")}
                  value={provider}
                  onChange={(event) => setProvider(event.target.value)}
                />
              </FormField>
            )}
            <ChoiceField
              variant="filter"
              className={styles.selectorField}
              label={t("Scope")}
              value={scope}
              onValueChange={(value) => {
                setScope(value as MemoryTarget["scope"]);
                setSubject("");
              }}
              options={[
                { value: "user", label: scopeLabels.user },
                { value: "agent", label: scopeLabels.agent },
                { value: "thread", label: scopeLabels.thread },
              ]}
            />
            {scope === "agent" ? (
              <div className={styles.selectorField}>
                <SearchPicker
                  label={t("Agent")}
                  placeholder={t("Agent")}
                  emptyMessage={t("No agents available.")}
                  value={subject}
                  onValueChange={(value) => setSubject(value ?? "")}
                  groups={[
                    {
                      label: t("Agents"),
                      options: [
                        ...(agents.data ?? []).map((item) => ({
                          value: item.id,
                          label: item.name,
                          description: item.id,
                        })),
                        ...(subject &&
                        !agents.data?.some((item) => item.id === subject)
                          ? [
                              {
                                value: subject,
                                label: subject,
                                description: t("Selected subject"),
                              },
                            ]
                          : []),
                      ],
                    },
                  ]}
                />
              </div>
            ) : scope === "thread" ? (
              <FormField
                hideLabel
                className={styles.selectorField}
                label={t("Thread ID")}
              >
                <Input
                  required
                  value={subject}
                  onChange={(event) => setSubject(event.target.value)}
                  placeholder={t("Thread ID")}
                />
              </FormField>
            ) : (
              <p className={styles.selectorHint}>
                {t("Only your memories in this workspace.")}
              </p>
            )}
            <Button
              className={styles.selectorAction}
              type="submit"
              disabled={incomplete}
            >
              {t("View")}
            </Button>
          </fieldset>
          {scope === "thread" && (
            <p className={styles.note}>
              {t(
                "Open a thread’s memories from its session, or enter its stable Thread ID. Access follows the thread’s current run, not a historical run.",
              )}
            </p>
          )}
          {target && (
            <p className={styles.note}>
              {t("Viewing {{provider}} · {{scope}} · {{subject}}", {
                provider:
                  providers.data?.find((item) => item.id === target.provider_id)
                    ?.name ?? target.provider_id,
                scope: scopeLabels[target.scope],
                subject:
                  target.scope === "user"
                    ? t("Current user")
                    : (agents.data?.find(
                        (item) => item.id === target.subject_id,
                      )?.name ?? target.subject_id),
              })}
            </p>
          )}
          <ErrorNotice error={providers.error ?? agents.error} />
        </form>
      }
    >
      {target ? (
        <MemoryContents
          key={JSON.stringify(memoryKey(target))}
          target={target}
          onEditing={setEditing}
        />
      ) : (
        <Empty
          icon={<BrainIcon aria-hidden="true" />}
          title={t("Choose whose memories to view")}
          description={t(
            "Select a provider and a scope above. Agent and thread memories require an exact subject; My memories uses your signed-in identity.",
          )}
        />
      )}
    </MemoriesFrame>
  );
}
