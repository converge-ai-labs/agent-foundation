import {
  ArrowSquareOutIcon,
  FilesIcon,
  UserCircleIcon,
} from "@phosphor-icons/react";
import { Button, ChoiceField, DisclosureSection, Switch } from "a13n-ui";
import { useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { providersPath } from "../providers/navigation";
import { memoriesPath } from "./api";
import { useMemoryProviders } from "./availability";
import { fileEntry, MemoryEntryFields } from "./entries";

type Entry = Schema["MemoryEntrySelection"];
type Kind = "personal" | "project";
const personalPurpose = "User preferences and personal facts.";
const projectPurpose = "Project requirements, decisions, and procedures.";
const mem0Types = new Set(["a13n.mem0-oss", "a13n.mem0-platform"]);

function asEntries(
  value: Schema["MemoryConfiguration"] | null | undefined,
): Entry[] {
  if (!value) return [];
  if ("entries" in value) return value.entries;
  const { provider_id, ...options } = value;
  return [
    {
      ...options,
      name: "preferences",
      mode: "records",
      description: personalPurpose,
      backend: { provider_id },
    },
  ];
}

export function MemoryPresets({
  value,
  onChange,
  readOnly = false,
  agentId,
  savedProviderId,
}: {
  value: Schema["MemoryConfiguration"] | null | undefined;
  onChange: (value: Schema["MemoryConfiguration"] | null) => void;
  readOnly?: boolean;
  agentId?: string;
  savedProviderId?: string;
}) {
  const { t } = useTranslation();
  const { workspace, basePath, can } = useWorkspace();
  const { providers } = useMemoryProviders();
  const id = useId();
  const [chosenProvider, setChosenProvider] = useState("");
  const removed = useRef<Partial<Record<Kind, Entry>>>({});
  const entries = asEntries(value);
  const available = (providers.data ?? []).filter(
    (item) =>
      mem0Types.has(item.type) && item.enabled && item.credential_configured,
  );
  const selectedProvider =
    available.find((item) => item.id === chosenProvider) ?? available[0];
  // Recognize the supplied purpose and scope, never a tool-prefix name alone.
  const personalIndex = entries.findIndex(
    (entry) =>
      entry.mode === "records" &&
      entry.description === personalPurpose &&
      entry.scope === "user",
  );
  const projectIndex = entries.findIndex(
    (entry) =>
      entry.mode === "documents" &&
      entry.description === projectPurpose &&
      entry.scope !== "user" &&
      ("type" in entry.backend
        ? entry.backend.type === "a13n.filesystem"
        : providers.data?.some(
            (item) =>
              "provider_id" in entry.backend &&
              item.id === entry.backend.provider_id &&
              item.type === "a13n.filesystem",
          )),
  );
  const personal = entries[personalIndex];
  const project = entries[projectIndex];
  const custom = entries
    .map((entry, index) => ({ entry, index }))
    .filter(({ index }) => index !== personalIndex && index !== projectIndex);
  const providerId =
    personal && "provider_id" in personal.backend
      ? personal.backend.provider_id
      : undefined;
  const currentProvider = providers.data?.find(
    (item) => item.id === providerId,
  );
  const providerUnavailable =
    !!providerId &&
    providers.isSuccess &&
    (!currentProvider?.enabled || !currentProvider.credential_configured);

  function update(index: number, entry: Entry) {
    onChange({
      entries: entries.map((item, i) => (i === index ? entry : item)),
    });
  }
  function toggle(kind: Kind, enabled: boolean) {
    const index = kind === "personal" ? personalIndex : projectIndex;
    if (!enabled) {
      removed.current[kind] = entries[index];
      const next = entries.filter((_, i) => i !== index);
      onChange(next.length ? { entries: next } : null);
      return;
    }
    let entry = removed.current[kind];
    if (!entry) {
      if (kind === "personal") {
        if (!selectedProvider) return;
        entry = {
          name: "preferences",
          description: personalPurpose,
          mode: "records",
          backend: { provider_id: selectedProvider.id },
          scope: "user",
          auto_recall: true,
        };
      } else entry = fileEntry(entries);
    }
    const stem = entry.name;
    let name = stem;
    for (let n = 2; entries.some((item) => item.name === name); n++)
      name = `${stem.slice(0, 20)}_${n}`;
    onChange({ entries: [...entries, { ...entry, name }] });
  }
  function remove(index: number) {
    const next = entries.filter((_, i) => i !== index);
    onChange(next.length ? { entries: next } : null);
  }
  function addCustom() {
    let name = "custom";
    for (let n = 2; entries.some((entry) => entry.name === name); n++)
      name = `custom_${n}`;
    onChange({
      entries: [...entries, { ...fileEntry(), name, description: "" }],
    });
  }
  const manageLink = can("memory_provider.read") && (
    <a
      className="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      href={providersPath("memory", "workspace", workspace.key)}
      target="_blank"
      rel="noopener noreferrer"
    >
      {t("Manage memory providers")}
      <ArrowSquareOutIcon size={14} aria-hidden="true" />
    </a>
  );
  return (
    <div className="flex min-w-0 flex-col gap-5">
      <div>
        <p className="font-medium">{t("What should this agent remember?")}</p>
        <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
          {t(
            "Help it understand people and build on past work. Enable either, or use both together.",
          )}
        </p>
      </div>
      <div className="divide-y rounded-xl border">
        <section className="p-5" aria-labelledby={`${id}-personal-title`}>
          <div className="flex items-start gap-3">
            <UserCircleIcon
              size={22}
              className="mt-0.5 shrink-0 text-muted-foreground"
              aria-hidden="true"
            />
            <div className="min-w-0 flex-1">
              <label
                id={`${id}-personal-title`}
                htmlFor={`${id}-personal`}
                className="font-medium"
              >
                {t("Personal preferences")}
              </label>
              <p
                id={`${id}-personal-description`}
                className="mt-1 text-sm leading-relaxed text-muted-foreground"
              >
                {t(
                  "Remember each person's language, working style, and background across conversations.",
                )}
              </p>
              <p className="mt-2 text-sm text-muted-foreground">
                {t("For example: “Answer in Chinese and keep it concise.”")}
              </p>
            </div>
            <Switch
              id={`${id}-personal`}
              aria-describedby={`${id}-personal-description`}
              checked={!!personal}
              disabled={
                readOnly ||
                (!personal && entries.length >= 16) ||
                (!personal && !selectedProvider && !removed.current.personal)
              }
              onCheckedChange={(checked) => toggle("personal", checked)}
            />
          </div>
          <div className="mt-4 grid gap-3 sm:ml-9">
            {personal ? (
              <>
                {providerId ? (
                  <ChoiceField
                    label={t("Stored with")}
                    readOnly={readOnly}
                    value={providerId}
                    options={[
                      ...available.map((item) => ({
                        value: item.id,
                        label: item.name,
                      })),
                      ...(!available.some((item) => item.id === providerId)
                        ? [
                            {
                              value: providerId,
                              label: currentProvider?.name ?? providerId,
                            },
                          ]
                        : []),
                    ]}
                    onValueChange={(next) =>
                      update(personalIndex, {
                        ...personal,
                        backend: { provider_id: next },
                      })
                    }
                  />
                ) : (
                  <p className="text-sm text-muted-foreground">
                    {t("Storage is configured in advanced settings.")}
                  </p>
                )}
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <label htmlFor={`${id}-recall`} className="text-sm">
                      {t("Automatic recall")}
                    </label>
                    <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                      {t(
                        "Bring relevant saved preferences into each reply. This does not save every message.",
                      )}
                    </p>
                  </div>
                  <Switch
                    id={`${id}-recall`}
                    checked={personal.auto_recall ?? true}
                    disabled={readOnly}
                    onCheckedChange={(checked) =>
                      update(personalIndex, {
                        ...personal,
                        auto_recall: checked,
                      })
                    }
                  />
                </div>
                {providerUnavailable && (
                  <p
                    role="alert"
                    className="text-sm text-destructive-foreground"
                  >
                    {t(
                      "The selected provider is unavailable or disabled. Choose another provider before saving.",
                    )}
                  </p>
                )}
              </>
            ) : available.length ? (
              <ChoiceField
                readOnly={readOnly}
                label={t("Stored with")}
                value={selectedProvider?.id ?? ""}
                options={available.map((item) => ({
                  value: item.id,
                  label: item.name,
                }))}
                onValueChange={setChosenProvider}
              />
            ) : (
              <p className="text-sm text-muted-foreground">
                {t(
                  !can("memory_provider.read")
                    ? "Ask your workspace administrator to connect Mem0 to enable personal preferences."
                    : providers.isPending
                      ? "Loading memory providers…"
                      : providers.isError
                        ? "Memory providers could not be loaded. Your configuration is unchanged."
                        : "Connect Mem0 in memory providers to enable personal preferences.",
                )}
              </p>
            )}
            {manageLink}
            {personal && (
              <MemoryDetails
                title={t("Personal preference settings")}
                entry={personal}
                readOnly={readOnly}
                preset
                onChange={(next) => update(personalIndex, next)}
              />
            )}
          </div>
        </section>
        <section className="p-5" aria-labelledby={`${id}-project-title`}>
          <div className="flex items-start gap-3">
            <FilesIcon
              size={22}
              className="mt-0.5 shrink-0 text-muted-foreground"
              aria-hidden="true"
            />
            <div className="min-w-0 flex-1">
              <label
                id={`${id}-project-title`}
                htmlFor={`${id}-project`}
                className="font-medium"
              >
                {t("Project memory")}
              </label>
              <p
                id={`${id}-project-description`}
                className="mt-1 text-sm leading-relaxed text-muted-foreground"
              >
                {t(
                  "Keep requirements, decisions, and procedures in documents you can review and edit.",
                )}
              </p>
              <p className="mt-2 text-sm text-muted-foreground">
                {t(
                  "For example: “We chose PostgreSQL; releases need a review.”",
                )}
              </p>
            </div>
            <Switch
              id={`${id}-project`}
              aria-describedby={`${id}-project-description`}
              checked={!!project}
              disabled={readOnly || (!project && entries.length >= 16)}
              onCheckedChange={(checked) => toggle("project", checked)}
            />
          </div>
          <div className="mt-4 grid gap-3 sm:ml-9">
            <p className="text-xs text-muted-foreground">
              {t(
                "File-based · Kept in the run environment. Files last as long as that environment's storage.",
              )}
            </p>
            {project && (
              <ChoiceField
                label={t("Use these documents in")}
                readOnly={readOnly}
                value={project.scope ?? "thread"}
                options={[
                  { value: "thread", label: t("Current thread") },
                  { value: "agent", label: t("This agent") },
                ]}
                onValueChange={(scope) =>
                  update(projectIndex, {
                    ...project,
                    scope: scope as "thread" | "agent",
                  })
                }
                description={t(
                  "To reuse files across conversations, reuse the same environment and choose This agent.",
                )}
              />
            )}
            {project && (
              <MemoryDetails
                title={t("Project memory settings")}
                entry={project}
                readOnly={readOnly}
                preset
                onChange={(next) => update(projectIndex, next)}
              />
            )}
          </div>
        </section>
      </div>
      <div className="space-y-2 text-xs leading-relaxed text-muted-foreground">
        <p>
          {t(
            "With both enabled, the agent uses each purpose to choose where to save. Preferences and project documents stay separate.",
          )}
        </p>
        <p>
          {t(
            "Turning an option off does not delete saved memories. Save the agent to apply your changes.",
          )}
        </p>
      </div>
      <ErrorNotice error={providers.error} />
      {agentId && (
        <a
          className="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
          href={memoriesPath(basePath, {
            scope: "agent",
            subject_id: agentId,
            provider_id: savedProviderId,
          })}
          target="_blank"
          rel="noopener noreferrer"
        >
          {t("View agent memories")}
          <ArrowSquareOutIcon size={14} aria-hidden="true" />
        </a>
      )}
      {custom.map(({ entry, index }) => (
        <MemoryDetails
          key={index}
          title={entry.name || t("Custom memory")}
          entry={entry}
          readOnly={readOnly}
          onChange={(next) => update(index, next)}
          onRemove={() => remove(index)}
        />
      ))}
      {!readOnly && (
        <Button
          type="button"
          variant="outline"
          className="self-start"
          disabled={entries.length >= 16}
          onClick={addCustom}
        >
          {t("Add custom memory")}
        </Button>
      )}
    </div>
  );
}

function MemoryDetails({
  title,
  entry,
  onChange,
  onRemove,
  readOnly,
  preset = false,
}: {
  title: string;
  entry: Entry;
  onChange: (entry: Entry) => void;
  onRemove?: () => void;
  readOnly?: boolean;
  preset?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(!entry.description);
  return (
    <div className="min-w-0" onInvalidCapture={() => setOpen(true)}>
      <DisclosureSection title={title} open={open} onOpenChange={setOpen}>
        <MemoryEntryFields
          entry={entry}
          onChange={onChange}
          readOnly={readOnly}
          preset={preset}
        />
        {!readOnly && onRemove && (
          <Button type="button" variant="outline" onClick={onRemove}>
            {t("Remove entry")}
          </Button>
        )}
      </DisclosureSection>
      {!open && !preset && (
        <p className="mt-2 text-sm text-muted-foreground">
          {entry.description}
        </p>
      )}
    </div>
  );
}
