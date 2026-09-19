import {
  ChoiceField,
  FormField,
  Input,
  ReadOnlyField,
  SettingsRow,
  Switch,
  Textarea,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { useMemoryProviders } from "./availability";

type Entry = Schema["MemoryEntrySelection"];

export function fileEntry(existing: readonly Entry[] = []): Entry {
  let name = "project";
  for (let i = 2; existing.some((entry) => entry.name === name); i++)
    name = `project_${i}`;
  return {
    name,
    mode: "documents",
    description: "Project requirements, decisions, and procedures.",
    backend: {
      type: "filesystem",
      configuration: { storage: { root: "/memory" } },
    },
    scope: "thread",
  };
}

export function MemoryEntryFields({
  entry,
  onChange,
  readOnly = false,
  preset = false,
}: {
  entry: Entry;
  onChange: (entry: Entry) => void;
  readOnly?: boolean;
  preset?: boolean;
}) {
  const { t } = useTranslation();
  const { providers } = useMemoryProviders();
  const backend = entry.backend;
  const providerId = "provider_id" in backend ? backend.provider_id : undefined;
  const configured =
    "configuration" in backend ? backend.configuration : undefined;
  const storage: Record<string, unknown> =
    configured?.storage &&
    typeof configured.storage === "object" &&
    !Array.isArray(configured.storage)
      ? (configured.storage as Record<string, unknown>)
      : {};
  const target = providerId ?? "filesystem";
  function setStorage(key: "root" | "environment_id", next: string) {
    const changed = { ...storage, [key]: next };
    if (key === "environment_id" && !next) delete changed.environment_id;
    onChange({
      ...entry,
      backend: {
        type: "filesystem",
        configuration: { storage: changed },
      },
    });
  }
  return (
    <div className="grid min-w-0 gap-4">
      {!preset && (
        <>
          <FormField
            label={t("Name")}
            readOnly={readOnly}
            description={t("A stable prefix for this entry's tools.")}
          >
            <Input
              required
              value={entry.name}
              pattern="[a-z][a-z0-9_]{0,23}"
              maxLength={24}
              onChange={(event) =>
                onChange({ ...entry, name: event.target.value })
              }
            />
          </FormField>
          <FormField label={t("Purpose")} readOnly={readOnly}>
            <Textarea
              required
              maxLength={2000}
              value={entry.description}
              onChange={(event) =>
                onChange({ ...entry, description: event.target.value })
              }
            />
          </FormField>
          <ChoiceField
            label={t("Memory backend")}
            readOnly={readOnly}
            value={target}
            options={[
              {
                value: "filesystem",
                label: t("File-based · Current environment"),
              },
              ...(providers.data ?? [])
                .filter(
                  (item) =>
                    item.id === providerId ||
                    (item.enabled &&
                      (item.credential_configured ||
                        item.type === "filesystem")),
                )
                .map((item) => ({ value: item.id, label: item.name })),
              ...(providerId &&
              !providers.data?.some((item) => item.id === providerId)
                ? [
                    {
                      value: providerId,
                      label: `${providerId} · ${t("Selected provider unavailable")}`,
                    },
                  ]
                : []),
            ]}
            onValueChange={(selected) => {
              const provider = providers.data?.find(
                (item) => item.id === selected,
              );
              const documents =
                selected === "filesystem" || provider?.type === "filesystem";
              onChange({
                name: entry.name,
                description: entry.description,
                mode: documents ? "documents" : "records",
                backend:
                  selected === "filesystem"
                    ? fileEntry().backend
                    : { provider_id: selected },
                scope: entry.scope,
                toolset: entry.toolset,
                recall_required: entry.recall_required,
              });
            }}
          />
          <ReadOnlyField label={t("Memory mode")}>
            {t(entry.mode === "documents" ? "Documents" : "Records")}
          </ReadOnlyField>
        </>
      )}
      {target === "filesystem" && (
        <>
          <FormField
            label={t("Memory directory")}
            readOnly={readOnly}
            description={t(
              "A path inside the selected environment. Persistence follows that environment's storage lifetime.",
            )}
          >
            <Input
              required
              value={
                typeof storage.root === "string" ? storage.root : "/memory"
              }
              onChange={(event) => setStorage("root", event.target.value)}
            />
          </FormField>
          <FormField
            label={t("Environment ID")}
            readOnly={readOnly}
            description={t(
              "Leave empty to use the current run environment. An explicit environment must be attached to the run.",
            )}
          >
            <Input
              value={
                typeof storage.environment_id === "string"
                  ? storage.environment_id
                  : ""
              }
              onChange={(event) =>
                setStorage("environment_id", event.target.value)
              }
            />
          </FormField>
        </>
      )}
      {!preset && (
        <>
          <ChoiceField
            label={t("Memory scope")}
            readOnly={readOnly}
            value={
              entry.scope ?? (entry.mode === "documents" ? "thread" : "all")
            }
            options={[
              ...(entry.mode === "records"
                ? [{ value: "all", label: t("All available scopes") }]
                : []),
              { value: "thread", label: t("Current thread") },
              { value: "agent", label: t("This agent") },
              { value: "user", label: t("Current user") },
            ]}
            onValueChange={(scope) =>
              onChange({
                ...entry,
                scope:
                  scope === "all"
                    ? null
                    : (scope as "thread" | "agent" | "user"),
              })
            }
          />
        </>
      )}
      {(
        [
          "toolset",
          "recall_required",
          ...(entry.mode === "records" && !preset ? ["auto_recall"] : []),
        ] as const
      ).map((key) => {
        const option = key as "toolset" | "recall_required" | "auto_recall";
        const label = t(
          option === "toolset"
            ? "Memory tools"
            : option === "auto_recall"
              ? "Automatic recall"
              : "Require available memory",
        );
        return readOnly ? (
          <ReadOnlyField key={option} label={label}>
            {t((entry[option] ?? option !== "recall_required") ? "On" : "Off")}
          </ReadOnlyField>
        ) : (
          <SettingsRow key={option} label={label}>
            <Switch
              aria-label={label}
              checked={entry[option] ?? option !== "recall_required"}
              onCheckedChange={(checked) =>
                onChange({ ...entry, [option]: checked })
              }
            />
          </SettingsRow>
        );
      })}
      {entry.mode === "documents" && (
        <SettingsRow
          label={t("Automatic organization")}
          description={t(
            "Organize completed work into memory. Uses the agent model and is off by default.",
          )}
        >
          <Switch
            aria-label={t("Automatic organization")}
            disabled={readOnly}
            checked={entry.auto_organize ?? false}
            onCheckedChange={(checked) =>
              onChange({ ...entry, auto_organize: checked })
            }
          />
        </SettingsRow>
      )}

      {entry.mode === "records" && (
        <>
          <FormField readOnly={readOnly} label={t("Recall limit")}>
            <Input
              type="number"
              required
              min={1}
              max={100}
              step={1}
              value={entry.recall_limit ?? 5}
              onChange={(event) =>
                onChange({ ...entry, recall_limit: Number(event.target.value) })
              }
            />
          </FormField>
          <FormField
            readOnly={readOnly}
            label={t("Similarity threshold")}
            description={t(
              "Optional, from 0 to 1. Leave empty to use the backend default.",
            )}
          >
            <Input
              type="number"
              min={0}
              max={1}
              step="any"
              value={entry.recall_threshold ?? ""}
              onChange={(event) =>
                onChange({
                  ...entry,
                  recall_threshold:
                    event.target.value === ""
                      ? null
                      : Number(event.target.value),
                })
              }
            />
          </FormField>
          <FormField readOnly={readOnly} label={t("Recall timeout (seconds)")}>
            <Input
              type="number"
              required
              min={0.001}
              max={300}
              step="any"
              value={entry.recall_timeout ?? 2}
              onChange={(event) =>
                onChange({
                  ...entry,
                  recall_timeout: Number(event.target.value),
                })
              }
            />
          </FormField>
        </>
      )}
      <ErrorNotice error={providers.error} />
    </div>
  );
}
