import { PlusIcon, XIcon } from "@phosphor-icons/react";
import { Button, Menu, MenuItem, MenuPopup, MenuTrigger } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { FieldHelp } from "../../../shared/forms";
import {
  automaticSources,
  toggleDiscoverySource,
  usesToolDiscovery,
  type SearchSource,
} from "../tool-search";
import type { AgentDraft } from "./draft";
import styles from "./editor.module.css";

export function ToolSearchSources({
  draft,
  sources,
  readOnly,
}: {
  draft: AgentDraft;
  sources: SearchSource[];
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  const choices =
    draft.toolSearch.automatic != null
      ? automaticSources(sources).map((source) =>
          source.id === "all" ? { ...source, label: t("MCP tools") } : source,
        )
      : sources;
  const selected = choices.filter((source) =>
    usesToolDiscovery(draft.toolSearch, source),
  );
  const remaining = choices.filter(
    (source) => !usesToolDiscovery(draft.toolSearch, source),
  );
  const direct = remaining.filter((source) => source.kind !== "plugins");
  const toggle = (source: SearchSource, checked: boolean) =>
    draft.setToolSearch((current) =>
      toggleDiscoverySource(current, source, checked, sources),
    );
  return (
    <div className={styles.searchScope}>
      <div className={styles.searchGroupHeading}>
        <span className={styles.searchLabel}>
          {t("On-demand tools")}
          <FieldHelp label={t("About on-demand tools")}>
            <p>
              {t(
                "Computer, Browser, Platform and MCP tools use on-demand discovery by default. Add other sources here to include them.",
              )}
            </p>
            <p className="mt-2">
              {t(
                "This changes how tools reach the model. It does not enable tools or change their permissions.",
              )}
            </p>
          </FieldHelp>
        </span>
        {!readOnly && (
          <Menu>
            <MenuTrigger
              render={
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!remaining.length}
                />
              }
            >
              <PlusIcon aria-hidden="true" />
              {t("Add tool source")}
            </MenuTrigger>
            <MenuPopup align="end">
              {remaining.map((source) => (
                <MenuItem
                  key={`${source.kind}/${source.id}`}
                  onClick={() => toggle(source, true)}
                >
                  {source.label}
                </MenuItem>
              ))}
            </MenuPopup>
          </Menu>
        )}
      </div>
      {selected.length ? (
        <ul className={styles.searchSelectedSources}>
          {selected.map((source) => (
            <li key={`${source.kind}/${source.id}`}>
              <span>{source.label}</span>
              {!source.available && (
                <small>
                  {t(source.id === "all" ? "No connections" : "Not enabled")}
                </small>
              )}
              {!readOnly && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  aria-label={t("Provide {{name}} directly", {
                    name: source.label,
                  })}
                  onClick={() => toggle(source, false)}
                >
                  <XIcon aria-hidden="true" />
                </Button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p role="status" className={styles.searchEmpty}>
          {t("No sources selected. Add a source to use on-demand discovery.")}
        </p>
      )}
      {!!direct.length && (
        <div className={styles.searchDirect}>
          <span className={styles.searchLabel}>
            {t("Provided directly")}
            <FieldHelp label={t("About directly provided tools")}>
              {t(
                "The model receives these tool definitions directly. Add a source above to discover its tools on demand instead.",
              )}
            </FieldHelp>
          </span>
          <p>{direct.map((source) => source.label).join(" · ")}</p>
        </div>
      )}
    </div>
  );
}
