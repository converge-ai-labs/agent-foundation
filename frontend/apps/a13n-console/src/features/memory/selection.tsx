import { ArrowSquareOutIcon } from "@phosphor-icons/react";
import {
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  ReadOnlyField,
  SettingsRow,
  Switch,
} from "a13n-ui";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { providersPath } from "../providers/navigation";
import { useMemoryProviders } from "./availability";
import { memoriesPath } from "./api";

export function AgentMemorySelection({
  value,
  onChange,
  readOnly = false,
  agentId,
  savedProviderId,
}: {
  value: Schema["MemorySelection"] | null | undefined;
  onChange: (value: Schema["MemorySelection"] | null) => void;
  readOnly?: boolean;
  agentId?: string;
  savedProviderId?: string;
}) {
  const { t } = useTranslation(),
    { workspace, can, basePath } = useWorkspace();
  const id = useId();
  const [advanced, setAdvanced] = useState(false);
  const { providers } = useMemoryProviders();
  const selected = providers.data?.find(
    (item) => item.id === value?.provider_id,
  );
  const options = (providers.data ?? []).filter(
    (item) =>
      item.id === value?.provider_id ||
      (item.enabled && item.credential_configured),
  );
  function toggle(
    key: "auto_recall" | "toolset" | "recall_required",
    label: string,
    description: string,
    defaultValue: boolean,
  ) {
    if (!value) return null;
    const checked = value[key] ?? defaultValue;
    if (readOnly)
      return (
        <ReadOnlyField label={t(label)} description={t(description)}>
          {t(checked ? "On" : "Off")}
        </ReadOnlyField>
      );
    return (
      <SettingsRow
        label={t(label)}
        description={t(description)}
        controlId={`${id}-${key}`}
      >
        <Switch
          id={`${id}-${key}`}
          aria-describedby={`${id}-${key}-description`}
          checked={checked}
          onCheckedChange={(checked) => onChange({ ...value, [key]: checked })}
        />
      </SettingsRow>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <ChoiceField
        readOnly={readOnly}
        label={t("Memory provider")}
        value={value?.provider_id ?? "off"}
        onValueChange={(provider_id) =>
          onChange(provider_id === "off" ? null : { ...value, provider_id })
        }
        options={[
          { value: "off", label: t("Off") },
          ...options.map((item) => ({
            value: item.id,
            label: `${item.name} · ${t(item.workspace_id ? "Workspace" : "Organization")}${!item.enabled || !item.credential_configured ? ` · ${t("Unavailable")}` : ""}`,
          })),
          ...(value && !selected
            ? [
                {
                  value: value.provider_id,
                  label: `${value.provider_id} · ${t("Selected provider unavailable")}`,
                },
              ]
            : []),
        ]}
        description={t(
          "Turning memory off does not delete stored records. Switching providers does not migrate them.",
        )}
      />
      <ErrorNotice error={providers.error} />
      {value &&
        providers.isSuccess &&
        (!selected?.enabled || !selected.credential_configured) && (
          <p role="alert" className="text-sm text-destructive-foreground">
            {t(
              "The selected provider is unavailable or disabled. Choose another provider before saving.",
            )}
          </p>
        )}
      {!can("memory_provider.read") && (
        <p className="text-sm text-muted-foreground">
          {t(
            "You do not have permission to browse memory providers. The saved selection is retained.",
          )}
        </p>
      )}
      {can("memory_provider.read") && (
        <a
          className="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
          href={providersPath("memory", "workspace", workspace.key)}
        >
          {t("Manage memory providers")}
          <ArrowSquareOutIcon size={14} aria-hidden="true" />
        </a>
      )}
      {agentId && (
        <a
          className="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
          href={memoriesPath(basePath, {
            scope: "agent",
            subject_id: agentId,
            provider_id: savedProviderId,
          })}
        >
          {t("View agent memories")}
          <ArrowSquareOutIcon size={14} aria-hidden="true" />
        </a>
      )}
      {value && (
        <>
          <ChoiceField
            readOnly={readOnly}
            label={t("Memory scope")}
            value={value.scope ?? "all"}
            onValueChange={(scope) =>
              onChange({
                ...value,
                scope:
                  scope === "all"
                    ? null
                    : (scope as "thread" | "agent" | "user"),
              })
            }
            options={[
              { value: "all", label: t("All available scopes") },
              { value: "thread", label: t("Current thread") },
              { value: "agent", label: t("This agent") },
              { value: "user", label: t("Current user") },
            ]}
            description={t(
              "Recall uses only authorized scopes. User memory is limited to the current workspace.",
            )}
          />
          {toggle(
            "auto_recall",
            "Automatic recall",
            "Retrieve relevant memories before model work. This does not automatically save conversation transcripts.",
            true,
          )}
          {toggle(
            "toolset",
            "Memory tools",
            "Allow the agent to search, list, and explicitly add memories. Editing and deletion remain management actions.",
            true,
          )}
          <DisclosureSection
            title={t("Recall settings")}
            open={advanced}
            onOpenChange={setAdvanced}
          >
            <div
              className="flex flex-col gap-4"
              onInvalidCapture={() => setAdvanced(true)}
            >
              <FormField readOnly={readOnly} label={t("Recall limit")}>
                <Input
                  type="number"
                  required
                  min={1}
                  max={100}
                  step={1}
                  value={value.recall_limit ?? 5}
                  onChange={(event) =>
                    onChange({
                      ...value,
                      recall_limit: Number(event.target.value),
                    })
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
                  value={value.recall_threshold ?? ""}
                  onChange={(event) =>
                    onChange({
                      ...value,
                      recall_threshold:
                        event.target.value === ""
                          ? null
                          : Number(event.target.value),
                    })
                  }
                />
              </FormField>
              <FormField
                readOnly={readOnly}
                label={t("Recall timeout (seconds)")}
              >
                <Input
                  type="number"
                  required
                  min={0.001}
                  max={300}
                  step="any"
                  value={value.recall_timeout ?? 2}
                  onChange={(event) =>
                    onChange({
                      ...value,
                      recall_timeout: Number(event.target.value),
                    })
                  }
                />
              </FormField>
              {toggle(
                "recall_required",
                "Require successful recall",
                "Stop before model work if automatic recall fails, instead of continuing without memory context.",
                false,
              )}
            </div>
          </DisclosureSection>
        </>
      )}
    </div>
  );
}
