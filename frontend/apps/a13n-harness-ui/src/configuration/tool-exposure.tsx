import { useState } from "react";
import { Button, ChoiceField, FormField, Input, Textarea } from "a13n-ui";
import { readDocument, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";

export function ToolExposureFields({
  source,
  onChange,
  path = [],
}: {
  source: string;
  onChange: (source: string) => void;
  path?: string[];
}) {
  const [name, setName] = useState("");
  const document = readDocument(source);
  const raw = path.reduce((value, key) => value?.[key], document?.toJS());
  const overrides: Record<string, string> = raw?.tool_exposure ?? {};
  const set = (key: string, value: unknown) =>
    onChange(updateDocument(source, [...path, key], value));
  return (
    <div className={styles.stack}>
      <ChoiceField
        label="Default tool exposure"
        value={raw?.exposure ?? "direct"}
        options={[
          { value: "direct", label: "Direct — schemas always visible" },
          { value: "progressive", label: "Progressive — discover when needed" },
        ]}
        onValueChange={(value) => set("exposure", value)}
        description="Controls schema visibility. Tool selection and permissions stay unchanged."
      />
      <FormField
        label="Toolset purpose"
        description="Describe when to use these tools. Required for progressive sources unless the built-in toolset or plugin provides a purpose."
      >
        <Textarea
          rows={2}
          maxLength={512}
          value={raw?.description ?? ""}
          onChange={(event) =>
            set("description", event.target.value || undefined)
          }
        />
      </FormField>
      <details className={styles.details}>
        <summary>
          Per-tool overrides
          {Object.keys(overrides).length
            ? ` (${Object.keys(overrides).length})`
            : ""}
        </summary>
        <div className={styles.stack}>
          <small>
            Use exact final tool names. An override takes precedence over this
            source's default.
          </small>
          {Object.entries(overrides).map(([tool, exposure]) => (
            <div key={tool} className={styles.actions}>
              <ChoiceField
                label={tool}
                value={exposure}
                options={[
                  { value: "direct", label: "Direct" },
                  { value: "progressive", label: "Progressive" },
                ]}
                onValueChange={(value) =>
                  set("tool_exposure", { ...overrides, [tool]: value })
                }
              />
              <Button
                variant="ghost"
                aria-label={`Remove override ${tool}`}
                onClick={() => {
                  const next = { ...overrides };
                  delete next[tool];
                  set("tool_exposure", next);
                }}
              >
                Remove
              </Button>
            </div>
          ))}
          <FormField label="Exact tool name">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </FormField>
          <div>
            <Button
              variant="outline"
              disabled={!name.trim() || name.trim() in overrides}
              onClick={() => {
                set("tool_exposure", {
                  ...overrides,
                  [name.trim()]: "progressive",
                });
                setName("");
              }}
            >
              Add override
            </Button>
          </div>
        </div>
      </details>
    </div>
  );
}

export function ProgressiveSearchFields({
  source,
  onChange,
}: {
  source: string;
  onChange: (source: string) => void;
}) {
  const raw = readDocument(source)?.toJS() ?? {};
  return (
    <div className={styles.stack}>
      <ChoiceField
        label="Progressive search"
        value={raw.progressive_search ?? "harness"}
        options={[
          { value: "harness", label: "Harness discovery" },
          { value: "native", label: "Provider-native search" },
        ]}
        description="Harness provides discovery and execution tools. Native search requires model support; unsupported models fail explicitly."
        onValueChange={(value) =>
          onChange(
            updateDocument(
              updateDocument(source, ["progressive_search"], value),
              ["progressive_search_strategy"],
              undefined,
            ),
          )
        }
      />
      {raw.progressive_search === "native" && (
        <ChoiceField
          label="Native search strategy"
          value={raw.progressive_search_strategy ?? "default"}
          options={[
            { value: "default", label: "Provider default" },
            { value: "bm25", label: "BM25" },
            { value: "regex", label: "Regex" },
          ]}
          onValueChange={(value) =>
            onChange(
              updateDocument(
                source,
                ["progressive_search_strategy"],
                value === "default" ? undefined : value,
              ),
            )
          }
        />
      )}
    </div>
  );
}
