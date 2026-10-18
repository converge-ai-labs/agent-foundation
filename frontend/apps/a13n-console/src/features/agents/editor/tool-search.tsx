import { Button, Checkbox, ChoiceField, FormField, Input } from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";
import { Section } from "../../../shared/page";
import { isRecord } from "../../../service-client";
import { jsonObject } from "../../../shared/forms";
import type { useAgentChoices } from "../choices";
import { displayName } from "../connections";
import type { SearchGroup, ToolSearchDraft } from "../tool-search";
import type { AgentDraft } from "./draft";
import styles from "./editor.module.css";

export function ToolSearchSection({
  draft,
  choices,
  readOnly,
  mark,
  error,
}: {
  draft: AgentDraft;
  choices: ReturnType<typeof useAgentChoices>;
  readOnly: boolean;
  mark?: ReactNode;
  error?: Error;
}) {
  const { t } = useTranslation();
  const value = draft.toolSearch;
  const groups = value.groups;
  let platformSelected = !!draft.initial.platform_tools?.groups?.length;
  try {
    const advanced = jsonObject(draft.advanced);
    const platform = advanced.platform_tools;
    platformSelected = !!(
      isRecord(platform) &&
      Array.isArray(platform.groups) &&
      platform.groups.length
    );
  } catch {
    /* Advanced configuration reports its own JSON errors. */
  }
  const sources = [
    ...draft.connections.map(({ connection_id }) => {
      const connection = choices.data?.connections.find(
        (item) => item.id === connection_id,
      );
      return {
        kind: "connections" as const,
        id: connection_id,
        label: connection ? displayName(connection) : connection_id,
        available: true,
      };
    }),
    ...(draft.initial.plugins ?? []).map((plugin) => ({
      kind: "plugins" as const,
      id: plugin.instance_name,
      label: plugin.instance_name,
      available: true,
    })),
    {
      kind: "platform" as const,
      id: "platform",
      label: t("Selected platform tools"),
      available: platformSelected,
    },
  ];
  function update(index: number, change: Partial<SearchGroup>) {
    draft.setToolSearch((current) => ({
      ...current,
      groups: current.groups.map((group, i) =>
        i === index ? { ...group, ...change } : group,
      ),
    }));
  }
  function selected(group: SearchGroup, source: (typeof sources)[number]) {
    return source.kind === "platform"
      ? group.platform === true
      : group[source.kind]?.includes(source.id) === true;
  }
  return (
    <Section
      title={t("Tool discovery")}
      description={t(
        "Group selected tools and choose how the model discovers them.",
      )}
      actions={mark}
    >
      <div className="grid gap-4">
        <ChoiceField
          label={t("Discovery mode")}
          readOnly={readOnly}
          value={value.mode}
          options={[
            { value: "direct", label: t("Source defaults") },
            { value: "proxy", label: t("Grouped proxy search") },
            { value: "native", label: t("Native tool search") },
          ]}
          onValueChange={(mode) =>
            draft.setToolSearch((current) => ({
              ...current,
              mode: mode as ToolSearchDraft["mode"],
            }))
          }
          description={t(
            value.mode === "native"
              ? "Requires a model with native tool search. Discovered tools run through the Service's existing permissions and connections."
              : value.mode === "proxy"
                ? "Harness provides grouped search and invocation. Ungrouped sources keep their own loading settings."
                : "Each source keeps its own loading settings.",
          )}
        />
        {value.mode !== "direct" && (
          <>
            {groups.map((group, index) => (
              <div key={index} className={styles.searchGroup}>
                <div className={styles.searchGroupHeading}>
                  <strong>{group.name || t("New group")}</strong>
                  {!readOnly && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-label={t("Remove group {{name}}", {
                        name: group.name,
                      })}
                      onClick={() =>
                        draft.setToolSearch((current) => ({
                          ...current,
                          groups: current.groups.filter((_, i) => i !== index),
                        }))
                      }
                    >
                      {t("Remove")}
                    </Button>
                  )}
                </div>
                <div className={styles.grid}>
                  <FormField
                    label={t("Group name")}
                    description={t(
                      "Used in tool names, such as knowledge__lookup.",
                    )}
                  >
                    <Input
                      readOnly={readOnly}
                      value={group.name}
                      maxLength={32}
                      onChange={(event) =>
                        update(index, { name: event.target.value })
                      }
                    />
                  </FormField>
                  <FormField label={t("Group description")}>
                    <Input
                      readOnly={readOnly}
                      value={group.description}
                      maxLength={512}
                      onChange={(event) =>
                        update(index, { description: event.target.value })
                      }
                    />
                  </FormField>
                </div>
                <fieldset
                  disabled={readOnly}
                  className="fieldset-reset grid gap-2"
                >
                  <legend className="mb-2 text-sm font-medium">
                    {t("Tool sources")}
                  </legend>
                  {sources.map((source) => (
                    <label
                      key={`${source.kind}/${source.id}`}
                      className={styles.searchSource}
                    >
                      <Checkbox
                        checked={selected(group, source)}
                        disabled={
                          !source.available ||
                          groups.some(
                            (other, i) =>
                              i !== index && selected(other, source),
                          )
                        }
                        onCheckedChange={(checked) =>
                          update(
                            index,
                            source.kind === "platform"
                              ? { platform: checked === true }
                              : {
                                  [source.kind]: checked
                                    ? [...(group[source.kind] ?? []), source.id]
                                    : (group[source.kind] ?? []).filter(
                                        (id) => id !== source.id,
                                      ),
                                },
                          )
                        }
                      />
                      <span>{source.label}</span>
                    </label>
                  ))}
                </fieldset>
              </div>
            ))}
            {!groups.length && (
              <p className="text-muted-foreground">
                {t("Add a group and select its tool sources.")}
              </p>
            )}
            {!readOnly && (
              <div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    let name = "tools";
                    for (
                      let index = 2;
                      groups.some((group) => group.name === name);
                      index++
                    )
                      name = `tools${index}`;
                    draft.setToolSearch((current) => ({
                      ...current,
                      groups: [
                        ...current.groups,
                        {
                          name,
                          description: "",
                          connections: [],
                          plugins: [],
                          platform: false,
                        },
                      ],
                    }));
                  }}
                >
                  {t("Add group")}
                </Button>
              </div>
            )}
          </>
        )}
        <ErrorNotice error={error} />
      </div>
    </Section>
  );
}
