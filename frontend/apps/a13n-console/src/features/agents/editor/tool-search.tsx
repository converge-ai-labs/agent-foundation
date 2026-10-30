import formStyles from "../../../shared/forms/forms.module.css";
import { Button, Checkbox, ChoiceField, FormField, Input } from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";
import type { useAgentChoices } from "../choices";
import { displayName } from "../connections";
import { useToolsetCatalog } from "../toolsets";
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
  const catalog = useToolsetCatalog();
  const platformSelected = !!catalog.data?.items.some(
    (group) =>
      group.platform &&
      (draft.toolsets[group.key]?.enabled ?? group.default_enabled) &&
      group.tools.some(
        (tool) =>
          draft.toolsets[group.key]?.tools?.[tool.key]?.enabled ??
          tool.default_enabled,
      ),
  );
  const sources = [
    ...(["environment", "web"] as const).map((id) => ({
      kind: "capabilities" as const,
      id,
      label: id === "environment" ? t("Environment tools") : t("Web tools"),
      available: true,
    })),
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
    ...draft.plugins.map((plugin) => ({
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
      : (group[source.kind] as readonly string[] | undefined)?.includes(
          source.id,
        ) === true;
  }
  return (
    <>
      {mark}
      <div data-slot="tool-discovery-fields" className="grid gap-4">
        <ChoiceField
          popupClassName={formStyles.compactChoicePopup}
          label={t("Mode")}
          readOnly={readOnly}
          value={value.mode}
          options={[
            { value: "direct", label: t("Default") },
            { value: "proxy", label: t("Search groups") },
          ]}
          onValueChange={(mode) =>
            draft.setToolSearch((current) => ({
              ...current,
              mode: mode as ToolSearchDraft["mode"],
            }))
          }
          description={t(
            value.mode === "proxy"
              ? "Search groups to find tools when needed."
              : "Use each source’s tool settings.",
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
            {(!readOnly || !groups.length) && (
              <div className={styles.searchFooter}>
                {!groups.length && (
                  <p>{t("Add a group and select its tool sources.")}</p>
                )}
                {!readOnly && (
                  <>
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
                  </>
                )}
              </div>
            )}
          </>
        )}
        <ErrorNotice error={error} />
      </div>
    </>
  );
}
