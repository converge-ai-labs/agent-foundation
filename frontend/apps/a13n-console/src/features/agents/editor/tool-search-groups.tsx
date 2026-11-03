import { PlusIcon, XIcon } from "@phosphor-icons/react";
import {
  Button,
  Checkbox,
  DisclosureSection,
  FormField,
  Input,
  Textarea,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHelp } from "../../../shared/forms";
import {
  groupHasSource,
  type SearchGroup,
  type SearchSource,
} from "../tool-search";
import type { AgentDraft } from "./draft";
import agentStyles from "../agents.module.css";
import styles from "./editor.module.css";

export function ToolSearchGroups({
  draft,
  sources,
  readOnly,
  error,
}: {
  draft: AgentDraft;
  sources: SearchSource[];
  readOnly: boolean;
  error?: Error;
}) {
  const { t } = useTranslation();
  const groups = draft.toolSearch.groups;
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  function update(index: number, change: Partial<SearchGroup>) {
    draft.setToolSearch((current) => ({
      ...current,
      groups: current.groups.map((group, i) =>
        i === index ? { ...group, ...change } : group,
      ),
    }));
  }
  return (
    <div className={styles.searchGroups}>
      <div className={styles.searchGroupHeading}>
        <span className={styles.searchLabel}>
          {t("Custom groups")}
          <FieldHelp label={t("About custom groups")}>
            {t(
              "Group related sources together. Sources outside these groups keep their own tool settings.",
            )}
          </FieldHelp>
        </span>
        {!readOnly && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={groups.length >= 128}
            onClick={() => {
              let name = "tools";
              for (
                let index = 2;
                groups.some((group) => group.name === name);
                index++
              )
                name = `tools${index}`;
              setOpenIndex(groups.length);
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
            <PlusIcon aria-hidden="true" />
            {t("Add group")}
          </Button>
        )}
      </div>
      {!groups.length && (
        <p className={styles.searchEmpty}>
          {t("Add a group and select its tool sources.")}
        </p>
      )}
      {groups.map((group, index) => {
        const selected = sources.filter((source) =>
          groupHasSource(group, source),
        );
        return (
          <DisclosureSection
            key={index}
            className={`${agentStyles.outlinedDisclosure} ${styles.searchGroup}`}
            open={openIndex === index || !!error}
            onOpenChange={(open) => setOpenIndex(open ? index : null)}
            title={group.name || t("New group")}
            summary={
              selected.length
                ? selected.map((source) => source.label).join(" · ")
                : t("No sources selected")
            }
            actions={
              !readOnly && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  aria-label={t("Remove group {{name}}", { name: group.name })}
                  onClick={() => {
                    setOpenIndex(null);
                    draft.setToolSearch((current) => ({
                      ...current,
                      groups: current.groups.filter((_, i) => i !== index),
                    }));
                  }}
                >
                  <XIcon aria-hidden="true" />
                </Button>
              )
            }
          >
            <div className={styles.searchGroupBody}>
              <FormField
                label={t("Group name")}
                labelAction={
                  <FieldHelp label={t("About group names")}>
                    {t("Used in tool names, such as knowledge__lookup.")}
                  </FieldHelp>
                }
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
              <FormField
                label={t("Group description")}
                labelAction={
                  <FieldHelp label={t("About group descriptions")}>
                    {t("Tell the model what these tools can help with.")}
                  </FieldHelp>
                }
              >
                <Textarea
                  readOnly={readOnly}
                  value={group.description}
                  rows={2}
                  maxLength={512}
                  onChange={(event) =>
                    update(index, { description: event.target.value })
                  }
                />
              </FormField>
              <fieldset disabled={readOnly} className={styles.searchSourceList}>
                <legend className={styles.searchLabel}>
                  {t("Tool sources")}
                </legend>
                {sources.map((source) => {
                  const ownedElsewhere = groups.some(
                    (other, i) => i !== index && groupHasSource(other, source),
                  );
                  return (
                    <label
                      key={`${source.kind}/${source.id}`}
                      className={styles.searchSource}
                    >
                      <Checkbox
                        checked={groupHasSource(group, source)}
                        disabled={ownedElsewhere}
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
                      <span className={styles.searchSourceLabel}>
                        {source.label}
                      </span>
                      {(!source.available || ownedElsewhere) && (
                        <small>
                          {t(
                            ownedElsewhere
                              ? "In another group"
                              : "No enabled tools",
                          )}
                        </small>
                      )}
                    </label>
                  );
                })}
              </fieldset>
            </div>
          </DisclosureSection>
        );
      })}
    </div>
  );
}
