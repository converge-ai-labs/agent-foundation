import {
  CaretDownIcon,
  FilesIcon,
  NotebookIcon,
  PlusIcon,
  UserCircleIcon,
  XIcon,
} from "@phosphor-icons/react";
import {
  Button,
  ChoiceField,
  Collapsible,
  CollapsiblePanel,
  CollapsibleTrigger,
  DisclosureSection,
  SettingsRow,
  Switch,
} from "a13n-ui";
import { useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { IconTile } from "../../shared/identity";
import { ManageProvidersLink } from "../providers/manage-link";
import { memoriesPath } from "./api";
import {
  eligibleMemoryProvider,
  useMemoryProviders,
  useWorkspaceMemoryProviderDefinitions,
} from "./availability";
import { fileEntry, MemoryEntryFields } from "./entries";
import styles from "./memory.module.css";

type Entry = Schema["MemoryEntrySelection"];
type Kind = "personal" | "project";
const personalPurpose = "User preferences and personal facts.";
const projectPurpose = "Project requirements, decisions, and procedures.";
const mem0Types = new Set(["mem0_oss", "mem0_platform"]);

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
  const { basePath, can } = useWorkspace();
  const { providers } = useMemoryProviders();
  const definitions = useWorkspaceMemoryProviderDefinitions();
  const removed = useRef<Partial<Record<Kind, Entry>>>({});
  const entries = asEntries(value);
  const available = (providers.data ?? []).filter(
    (item) =>
      mem0Types.has(item.type) &&
      eligibleMemoryProvider(item, definitions.data?.items ?? []),
  );
  const selectedProvider = available[0];
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
        ? entry.backend.type === "filesystem"
        : providers.data?.some(
            (item) =>
              "provider_id" in entry.backend &&
              item.id === entry.backend.provider_id &&
              item.type === "filesystem",
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
    definitions.isSuccess &&
    (!currentProvider ||
      !eligibleMemoryProvider(currentProvider, definitions.data.items));

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
  const manageLink = can("memory_provider.read") ? (
    <ManageProvidersLink category="memory" scope="workspace" variant="ghost" />
  ) : null;
  const personalUnavailable = !personal && !selectedProvider;
  const personalDescription = !personalUnavailable
    ? t("Language, working style, and background, remembered per person.")
    : t(
        !can("memory_provider.read")
          ? "Ask your workspace administrator to connect Mem0 to enable personal preferences."
          : providers.isPending
            ? "Loading memory providers…"
            : providers.isError
              ? "Memory providers could not be loaded. Your configuration is unchanged."
              : "Connect Mem0 in memory providers to enable personal preferences.",
      );
  return (
    <div className={styles.selection}>
      <div className={styles.group}>
        <PresetRow
          icon={<UserCircleIcon size={16} />}
          title={t("Personal preferences")}
          description={personalDescription}
          warning={personalUnavailable}
          checked={!!personal}
          disabled={
            readOnly ||
            (!personal && entries.length >= 16) ||
            (!personal && !selectedProvider && !removed.current.personal)
          }
          onCheckedChange={(checked) => toggle("personal", checked)}
        >
          {personal && (
            <>
              {providerId ? (
                <div className={styles.entryControl}>
                  <ChoiceField
                    className={styles.entryChoice}
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
                  {manageLink}
                </div>
              ) : (
                <p className={styles.entryHint}>
                  {t("Storage is configured in advanced settings.")}
                </p>
              )}
              <p className={styles.entryHint}>
                {t("For example: “Answer in Chinese and keep it concise.”")}
              </p>
              {providerUnavailable && (
                <p role="alert" className={styles.entryAlert}>
                  {t(
                    "The selected provider is unavailable or disabled. Choose another provider before saving.",
                  )}
                </p>
              )}
              <SettingsRow
                label={t("Automatic recall")}
                description={t(
                  "Bring relevant saved preferences into each reply. This does not save every message.",
                )}
              >
                <Switch
                  aria-label={t("Automatic recall")}
                  checked={personal.auto_recall ?? true}
                  disabled={readOnly}
                  onCheckedChange={(checked) =>
                    update(personalIndex, { ...personal, auto_recall: checked })
                  }
                />
              </SettingsRow>
              <RecallSettings
                entry={personal}
                readOnly={readOnly}
                onChange={(next) => update(personalIndex, next)}
              />
            </>
          )}
        </PresetRow>
        <PresetRow
          icon={<FilesIcon size={16} />}
          title={t("Project memory")}
          description={t(
            "Requirements, decisions, and procedures, kept as documents.",
          )}
          checked={!!project}
          disabled={readOnly || (!project && entries.length >= 16)}
          onCheckedChange={(checked) => toggle("project", checked)}
        >
          {project && (
            <>
              <ChoiceField
                className={styles.entryChoice}
                label={t("Scope")}
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
              <p className={styles.entryHint}>
                {t(
                  "For example: “We chose PostgreSQL; releases need a review.”",
                )}
              </p>
              <RecallSettings
                entry={project}
                readOnly={readOnly}
                onChange={(next) => update(projectIndex, next)}
              />
            </>
          )}
        </PresetRow>
        {custom.map(({ entry, index }) => (
          <CustomRow
            key={index}
            entry={entry}
            readOnly={readOnly}
            onChange={(next) => update(index, next)}
            onRemove={() => remove(index)}
          />
        ))}
        {!readOnly && (
          <Button
            type="button"
            variant="ghost"
            className={styles.addEntry}
            disabled={entries.length >= 16}
            onClick={addCustom}
          >
            <PlusIcon size={14} aria-hidden="true" />
            {t("Add custom memory")}
          </Button>
        )}
      </div>
      <div className={styles.footnote}>
        <p>{t("Turning an option off does not delete saved memories.")}</p>
        <div className={styles.footnoteActions}>
          {!personal && manageLink}
          {agentId && (
            <Button
              variant="outline"
              size="sm"
              render={
                <Link
                  to={memoriesPath(basePath, {
                    scope: "agent",
                    subject_id: agentId,
                    provider_id: savedProviderId,
                  })}
                />
              }
            >
              {t("View agent memories")}
            </Button>
          )}
        </div>
      </div>
      <ErrorNotice error={providers.error} />
    </div>
  );
}

/** One preset row: tile, purpose, a switch, and its settings once it is on. */
function PresetRow({
  icon,
  title,
  description,
  warning = false,
  checked,
  disabled,
  onCheckedChange,
  children,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  warning?: boolean;
  checked: boolean;
  disabled: boolean;
  onCheckedChange: (checked: boolean) => void;
  children?: ReactNode;
}) {
  const id = useId();
  return (
    <section className={styles.entry} aria-labelledby={`${id}-title`}>
      <div className={styles.entryHeader}>
        <IconTile size={32} tone="elevated">
          {icon}
        </IconTile>
        <span className={styles.entryCopy}>
          <label id={`${id}-title`} htmlFor={`${id}-switch`}>
            {title}
          </label>
          <small
            id={`${id}-description`}
            data-tone={warning ? "warning" : undefined}
          >
            {description}
          </small>
        </span>
        <Switch
          id={`${id}-switch`}
          aria-describedby={`${id}-description`}
          checked={checked}
          disabled={disabled}
          onCheckedChange={onCheckedChange}
        />
      </div>
      {checked && children && (
        <div className={styles.entryBody}>{children}</div>
      )}
    </section>
  );
}

/** A custom entry: the row names it, and opens its own fields in place. */
function CustomRow({
  entry,
  readOnly,
  onChange,
  onRemove,
}: {
  entry: Entry;
  readOnly: boolean;
  onChange: (entry: Entry) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(!entry.description);
  const name = entry.name || t("Custom memory");
  return (
    <div className={styles.entry} onInvalidCapture={() => setOpen(true)}>
      <Collapsible open={open} onOpenChange={setOpen}>
        <div className={styles.entryHeader}>
          <IconTile size={32} tone="elevated">
            <NotebookIcon size={16} />
          </IconTile>
          <CollapsibleTrigger
            aria-label={name}
            render={<button type="button" className={styles.entryTrigger} />}
          >
            <span className={styles.entryCopy}>
              <strong>{name}</strong>
              <small>{entry.description || t("Custom memory")}</small>
            </span>
            <CaretDownIcon
              size={14}
              className={open ? styles.caretOpen : styles.caret}
              aria-hidden="true"
            />
          </CollapsibleTrigger>
          {!readOnly && (
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={t("Remove entry")}
              onClick={onRemove}
            >
              <XIcon />
            </Button>
          )}
        </div>
        <CollapsiblePanel>
          <div className={styles.entryBody}>
            <MemoryEntryFields
              entry={entry}
              onChange={onChange}
              readOnly={readOnly}
            />
          </div>
        </CollapsiblePanel>
      </Collapsible>
    </div>
  );
}

/** The rare knobs of a preset, closed until someone asks for them. */
function RecallSettings({
  entry,
  onChange,
  readOnly,
}: {
  entry: Entry;
  onChange: (entry: Entry) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div className={styles.recall} onInvalidCapture={() => setOpen(true)}>
      <DisclosureSection
        title={t(
          entry.mode === "documents" ? "Storage and recall" : "Recall settings",
        )}
        open={open}
        onOpenChange={setOpen}
      >
        <MemoryEntryFields
          entry={entry}
          onChange={onChange}
          readOnly={readOnly}
          preset
        />
      </DisclosureSection>
    </div>
  );
}
