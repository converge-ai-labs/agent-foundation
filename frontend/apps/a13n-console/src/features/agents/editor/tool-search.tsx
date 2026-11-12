import { ChoiceField, DisclosureSection } from "a13n-ui";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";
import { FieldHelp } from "../../../shared/forms";
import formStyles from "../../../shared/forms/forms.module.css";
import { useToolsetCatalog } from "../toolsets";
import {
  automaticSearchGroups,
  automaticPolicy,
  searchSources,
  type ToolSearchDraft,
} from "../tool-search";
import type { AgentDraft } from "./draft";
import agentStyles from "../agents.module.css";
import { ToolSearchGroups } from "./tool-search-groups";
import {
  ToolSearchSources,
  ToolSearchConnections,
} from "./tool-search-sources";
import styles from "./editor.module.css";

export function ToolSearchSection({
  draft,
  readOnly,
  mark,
  error,
}: {
  draft: AgentDraft;
  readOnly: boolean;
  mark?: ReactNode;
  error?: Error;
}) {
  const { t } = useTranslation();
  const value = draft.toolSearch;
  const [expanded, setExpanded] = useState(false);
  const catalog = useToolsetCatalog(
    draft.connections.map((item) => item.connection_id),
  );
  if (!catalog.data)
    return catalog.error ? (
      <ErrorNotice error={catalog.error} />
    ) : (
      <p role="status">{t("Loading…")}</p>
    );
  const sources = searchSources(
    {
      toolsets: draft.toolsets,
      connection_tools: draft.connections,
      plugins: draft.plugins,
    },
    catalog.data,
  ).map((source) => ({
    ...source,
    label:
      source.kind === "plugins" || source.kind === "connections"
        ? source.label
        : t(source.label),
  }));
  const automatic = value.automatic != null;
  return (
    <>
      {mark}
      <div data-slot="tool-discovery-fields" className={styles.searchFields}>
        <ChoiceField
          popupClassName={formStyles.compactChoicePopup}
          label={t("Mode")}
          className={styles.searchMode}
          readOnly={readOnly}
          value={value.mode}
          labelAction={
            <FieldHelp label={t("About tool availability")}>
              <p>
                {t(
                  "Provide tools directly: the model receives tool definitions for direct use.",
                )}
              </p>
              <p className="mt-2">
                {t(
                  "Discover tools on demand: the model looks up tools and reads their parameters when needed, then calls them through a shared entry point.",
                )}
              </p>
              <p className="mt-2">
                {t(
                  "Native tool discovery: the model searches for tools, then calls them directly. No groups are needed.",
                )}
              </p>
            </FieldHelp>
          }
          options={[
            { value: "direct", label: t("Provide tools directly") },
            { value: "proxy", label: t("Discover tools on demand") },
            { value: "native", label: t("Native tool discovery") },
          ]}
          onValueChange={(mode) =>
            draft.setToolSearch((current) => ({
              ...current,
              mode: mode as ToolSearchDraft["mode"],
              automatic:
                mode === "proxy" &&
                !current.groups.length &&
                current.automatic == null
                  ? {}
                  : current.automatic,
            }))
          }
        />
        {value.mode !== "direct" && (
          <>
            <ToolSearchSources
              draft={draft}
              sources={sources}
              catalog={catalog.data}
              readOnly={readOnly}
            />
            {value.mode === "native" && (
              <ToolSearchConnections
                draft={draft}
                sources={sources}
                catalog={catalog.data}
                readOnly={readOnly}
              />
            )}
            {value.mode === "proxy" && (
              <DisclosureSection
                className={`${agentStyles.outlinedDisclosure} ${styles.searchAdvanced}`}
                title={t("Advanced grouping")}
                summary={
                  expanded || error
                    ? undefined
                    : automatic
                      ? t("Grouped by source")
                      : t("Custom groups")
                }
                open={expanded || !!error}
                onOpenChange={setExpanded}
              >
                <ChoiceField
                  label={t("Grouping")}
                  className={styles.searchMode}
                  value={automatic ? "automatic" : "custom"}
                  readOnly={readOnly}
                  labelAction={
                    <FieldHelp label={t("About grouping")}>
                      <p>
                        {t(
                          "Group by source creates one discovery group for each selected source.",
                        )}
                      </p>
                      <p className="mt-2">
                        {t(
                          "Custom groups let you organize related sources yourself, with a name and description for each group.",
                        )}
                      </p>
                    </FieldHelp>
                  }
                  options={[
                    { value: "automatic", label: t("Group by source") },
                    { value: "custom", label: t("Custom groups") },
                  ]}
                  onValueChange={(grouping) =>
                    draft.setToolSearch((current) => ({
                      ...current,
                      automatic:
                        grouping === "automatic"
                          ? automaticPolicy(current, sources)
                          : null,
                      groups:
                        grouping === "custom" && current.automatic != null
                          ? automaticSearchGroups(current, sources)
                          : current.groups,
                    }))
                  }
                />
                {automatic && (
                  <ToolSearchConnections
                    draft={draft}
                    sources={sources}
                    catalog={catalog.data}
                    readOnly={readOnly}
                  />
                )}
                {!automatic && (
                  <ToolSearchGroups
                    draft={draft}
                    sources={sources}
                    readOnly={readOnly}
                    error={error}
                  />
                )}
                {!!draft.plugins.length && (
                  <div className={styles.searchPluginNote}>
                    <span>{t("Plugins use their own settings")}</span>
                    <FieldHelp label={t("About plugin discovery")}>
                      {t(
                        "Plugins keep their own discovery settings. Use custom groups to include compatible plugins.",
                      )}
                    </FieldHelp>
                  </div>
                )}
              </DisclosureSection>
            )}
          </>
        )}
        <ErrorNotice error={error} />
      </div>
    </>
  );
}
