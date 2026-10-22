import { useState, type ReactNode } from "react";
import { Button, ChoiceField, FormField, Input, Textarea } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { Section } from "../../../shared/page";
import type { AgentDraft } from "./draft";

export interface ExposurePolicy {
  exposure?: "direct" | "progressive";
  tool_exposure?: Record<string, "direct" | "progressive">;
  description?: string | null;
}

/** Source policy is independent of tool selection and permissions. */
export function ToolExposureFields({
  value,
  onChange,
  readOnly = false,
  purpose,
}: {
  value: ExposurePolicy;
  onChange: (patch: ExposurePolicy) => void;
  readOnly?: boolean;
  purpose?: string;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const overrides = value.tool_exposure ?? {};
  return (
    <div className="grid gap-4">
      <ChoiceField
        label={t("Default tool exposure")}
        value={value.exposure ?? "direct"}
        readOnly={readOnly}
        options={[
          { value: "direct", label: t("Direct — schemas always visible") },
          {
            value: "progressive",
            label: t("Progressive — discover when needed"),
          },
        ]}
        onValueChange={(exposure) =>
          onChange({ exposure: exposure as ExposurePolicy["exposure"] })
        }
        description={t(
          "Controls schema visibility. Tool selection and permissions stay unchanged.",
        )}
      />
      <FormField
        label={t("Toolset purpose")}
        description={
          purpose ??
          t(
            "Describe when to use these tools. Required for progressive sources unless the plugin provides a purpose.",
          )
        }
      >
        <Textarea
          readOnly={readOnly}
          rows={2}
          maxLength={512}
          value={value.description ?? ""}
          onChange={(event) =>
            onChange({ description: event.target.value || null })
          }
        />
      </FormField>
      <details>
        <summary className="cursor-pointer text-sm font-medium">
          {t("Per-tool overrides")} ({Object.keys(overrides).length})
        </summary>
        <div className="mt-3 grid gap-3">
          <p className="text-xs text-muted-foreground">
            {t(
              "Use exact final tool names. Overrides take precedence over the source default.",
            )}
          </p>
          {Object.entries(overrides).map(([tool, exposure]) => (
            <div key={tool} className="flex flex-wrap items-end gap-2">
              <ChoiceField
                label={tool}
                value={exposure}
                readOnly={readOnly}
                options={[
                  { value: "direct", label: t("Direct") },
                  { value: "progressive", label: t("Progressive") },
                ]}
                onValueChange={(value) =>
                  onChange({
                    tool_exposure: {
                      ...overrides,
                      [tool]: value as "direct" | "progressive",
                    },
                  })
                }
              />
              {!readOnly && (
                <Button
                  type="button"
                  variant="ghost"
                  aria-label={t("Remove override {{name}}", { name: tool })}
                  onClick={() => {
                    const next = { ...overrides };
                    delete next[tool];
                    onChange({ tool_exposure: next });
                  }}
                >
                  {t("Remove")}
                </Button>
              )}
            </div>
          ))}
          {!readOnly && (
            <div className="flex flex-wrap items-end gap-2">
              <FormField label={t("Exact tool name")}>
                <Input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </FormField>
              <Button
                type="button"
                variant="outline"
                disabled={!name.trim() || name.trim() in overrides}
                onClick={() => {
                  onChange({
                    tool_exposure: {
                      ...overrides,
                      [name.trim()]: "progressive",
                    },
                  });
                  setName("");
                }}
              >
                {t("Add override")}
              </Button>
            </div>
          )}
        </div>
      </details>
    </div>
  );
}

export function ToolExposureSection({
  draft,
  readOnly,
  mark,
}: {
  draft: AgentDraft;
  readOnly: boolean;
  mark?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <Section
      title={t("Tool discovery")}
      description={t(
        "Choose how progressive tools are discovered. Configure exposure on each tool source.",
      )}
      actions={mark}
    >
      <div className="grid gap-4">
        <ChoiceField
          label={t("Progressive search")}
          value={draft.progressiveSearch}
          readOnly={readOnly}
          options={[
            { value: "harness", label: t("Harness discovery") },
            { value: "native", label: t("Provider-native search") },
          ]}
          description={t(
            "Native search requires model support. Unsupported models fail explicitly.",
          )}
          onValueChange={(value) => {
            draft.setProgressiveSearch(value as "harness" | "native");
            draft.setProgressiveSearchStrategy(null);
          }}
        />
        {draft.progressiveSearch === "native" && (
          <ChoiceField
            label={t("Native search strategy")}
            value={draft.progressiveSearchStrategy ?? "default"}
            readOnly={readOnly}
            options={[
              { value: "default", label: t("Provider default") },
              { value: "bm25", label: "BM25" },
              { value: "regex", label: t("Regex") },
            ]}
            onValueChange={(value) =>
              draft.setProgressiveSearchStrategy(
                value === "default" ? null : (value as "bm25" | "regex"),
              )
            }
          />
        )}
        {draft.plugins.map((plugin, index) => (
          <details key={plugin.instance_name}>
            <summary className="cursor-pointer text-sm font-medium">
              {plugin.instance_name}
            </summary>
            <div className="mt-3">
              <ToolExposureFields
                value={plugin}
                readOnly={readOnly}
                onChange={(patch) =>
                  draft.setPlugins((current) =>
                    current.map((item, i) =>
                      i === index ? { ...item, ...patch } : item,
                    ),
                  )
                }
              />
            </div>
          </details>
        ))}
      </div>
    </Section>
  );
}
