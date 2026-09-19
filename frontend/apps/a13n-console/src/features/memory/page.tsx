import type { Schema } from "../../shared/api";
import { ArrowSquareOutIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, SearchPicker } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";
import { Empty, ErrorNotice, Page } from "../../shared/feedback";
import { providersPath } from "../providers/navigation";
import { memoryKey, type MemoryTarget } from "./api";
import { FileMemoryBrowser } from "./documents";
import { MemoryContents } from "./contents";
import {
  useMemoryProviders,
  useMemoryProviderDefinitions,
  eligibleMemoryProvider,
} from "./availability";

export function MemoriesPage() {
  const [params, setParams] = useSearchParams();
  const { t } = useTranslation();
  const [mode, setMode] = useState("records");
  return (
    <>
      <div className="mb-5 flex gap-2">
        <Button
          variant={mode === "records" ? "default" : "outline"}
          onClick={() => setMode("records")}
        >
          {t("Mem0 records")}
        </Button>
        <Button
          variant={mode === "files" ? "default" : "outline"}
          onClick={() => setMode("files")}
        >
          {t("File-based memory")}
        </Button>
      </div>
      {mode === "files" ? (
        <FileMemoryBrowser />
      ) : (
        <MemoryPageSelection
          key={params.toString()}
          params={params}
          onSelect={setParams}
        />
      )}
    </>
  );
}

function MemoryPageSelection({
  params,
  onSelect,
}: {
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
  const definitions = useMemoryProviderDefinitions();
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
  return (
    <Page
      title={t("Memories")}
      description={t(
        "Inspect and manage explicit memories for one provider and subject. Nothing is combined across scopes.",
      )}
      actions={
        can("memory_provider.read") ? (
          <a
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
            href={providersPath("memory", "workspace", workspace.key)}
            target="_blank"
            rel="noopener noreferrer"
          >
            {t("Manage memory providers")}
            <ArrowSquareOutIcon size={14} aria-hidden="true" />
          </a>
        ) : undefined
      }
    >
      <form
        className="mb-6 rounded-lg bg-muted p-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (
            editing ||
            !provider.trim() ||
            (scope !== "user" && !subject.trim())
          )
            return;
          const next = new URLSearchParams({
            provider: provider.trim(),
            scope,
          });
          if (scope !== "user") next.set("subject", subject.trim());
          onSelect(next);
        }}
      >
        <fieldset
          disabled={editing}
          className="grid min-w-0 gap-4 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_auto]"
        >
          {can("memory_provider.read") ? (
            <FormField label={t("Memory provider")}>
              <SearchPicker
                label={t("Memory provider")}
                placeholder={t("Choose a memory provider…")}
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
                        description: `${t(item.workspace_id ? "Workspace" : "Organization")} · ${item.id}${!eligible(item) ? ` · ${t("Unavailable")}` : ""}`,
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
            </FormField>
          ) : (
            <FormField label={t("Memory provider ID")}>
              <Input
                required
                value={provider}
                onChange={(event) => setProvider(event.target.value)}
              />
            </FormField>
          )}
          <ChoiceField
            label={t("Memory scope")}
            value={scope}
            onValueChange={(value) => {
              setScope(value as MemoryTarget["scope"]);
              setSubject("");
            }}
            options={[
              { value: "user", label: t("My memories") },
              { value: "agent", label: t("Agent") },
              { value: "thread", label: t("Thread") },
            ]}
          />
          {scope === "agent" ? (
            <FormField label={t("Agent")}>
              <SearchPicker
                label={t("Agent")}
                placeholder={t("Choose an agent…")}
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
            </FormField>
          ) : scope === "thread" ? (
            <FormField label={t("Thread ID")}>
              <Input
                required
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
                placeholder="thr_…"
              />
            </FormField>
          ) : (
            <p className="self-end pb-2 text-sm text-muted-foreground">
              {t("Only your memories in this workspace.")}
            </p>
          )}
          <Button
            className="self-end"
            type="submit"
            variant="outline"
            disabled={!provider.trim() || (scope !== "user" && !subject.trim())}
          >
            {t("View memories")}
          </Button>
        </fieldset>
        {scope === "thread" && (
          <p className="mt-3 text-xs text-muted-foreground">
            {t(
              "Open a thread’s memories from its session, or enter its stable Thread ID. Access follows the thread’s current run, not a historical run.",
            )}
          </p>
        )}
        <ErrorNotice error={providers.error ?? agents.error} />
      </form>
      {target ? (
        <>
          <p className="mb-4 break-words text-xs text-muted-foreground">
            {t("Viewing {{provider}} · {{scope}} · {{subject}}", {
              provider:
                providers.data?.find((item) => item.id === target.provider_id)
                  ?.name ?? target.provider_id,
              scope: t(
                target.scope === "user"
                  ? "My memories"
                  : target.scope === "agent"
                    ? "Agent"
                    : "Thread",
              ),
              subject: target.subject_id ?? t("Current user"),
            })}
          </p>
          <MemoryContents
            key={JSON.stringify(memoryKey(target))}
            target={target}
            onEditing={setEditing}
          />
        </>
      ) : (
        <Empty
          title={t("Choose whose memories to view")}
          description={t(
            "Select a provider and a scope above. Agent and thread memories require an exact subject; My memories uses your signed-in identity.",
          )}
        />
      )}
    </Page>
  );
}
