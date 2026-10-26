import {
  DotsThreeIcon,
  PlusIcon,
  PuzzlePieceIcon,
  TrashIcon,
} from "@phosphor-icons/react";
import { Badge, Button, Menu, MenuItem, MenuPopup, MenuTrigger } from "a13n-ui";
import { useEffect, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ListRow, ListRows, ListRowsEmpty } from "../../../shared/collection";
import { Confirm, useResourceRows } from "../../../shared/dialogs";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { Section } from "../../../shared/page";
import { usePluginCatalog } from "../plugin-catalog";
import { pluginValidationIndex, type Plugin } from "../plugins";
import type { AgentDraft } from "./draft";
import { PluginSettings } from "./plugin-settings";

export function PluginsSection({
  draft,
  readOnly,
  pending,
  mark,
  error,
}: {
  draft: AgentDraft;
  readOnly: boolean;
  pending: boolean;
  mark?: ReactNode;
  error?: unknown;
}) {
  const { t } = useTranslation();
  const catalog = usePluginCatalog();
  const editor = useResourceRows<{ initial?: Plugin }>();
  const section = useRef<HTMLDivElement>(null);
  const failureIndex = pluginValidationIndex(error);
  useEffect(() => {
    if (failureIndex !== undefined)
      section.current?.scrollIntoView?.({
        block: "center",
        behavior: "smooth",
      });
  }, [error, failureIndex]);
  // Stale data from a failed refresh cannot establish that a saved plugin is missing.
  const available = catalog.isSuccess ? catalog.data.items : undefined;
  const removed = (draft.initial.plugins ?? []).filter(
    (item) =>
      !draft.plugins.some((next) => next.instance_name === item.instance_name),
  );
  return (
    <div ref={section}>
      <Section
        title={t("Plugins")}
        description={t(
          "Extend this agent with plugins enabled in the deployment.",
        )}
        actions={
          <>
            {mark}
            {!readOnly && !!available?.length && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={pending || draft.plugins.length >= 128}
                onClick={(event) => editor.activate({}, event.currentTarget)}
              >
                <PlusIcon aria-hidden="true" />
                {t("Add plugin")}
              </Button>
            )}
          </>
        }
      >
        <ErrorNotice
          error={catalog.error}
          retry={() => void catalog.refetch()}
        />
        {catalog.isPending && !draft.plugins.length && <Loading />}
        {!!draft.plugins.length && (
          <ListRows>
            {draft.plugins.map((plugin, index) => {
              const definition = available?.find(
                (item) => item.plugin_key === plugin.plugin_key,
              );
              const missing = available !== undefined && !definition;
              const original = draft.initial.plugins?.find(
                (item) => item.instance_name === plugin.instance_name,
              );
              const changed =
                JSON.stringify(original) !== JSON.stringify(plugin);
              const groups = draft.toolSearch.groups.filter((group) =>
                group.plugins?.includes(plugin.instance_name),
              );
              return (
                <div key={plugin.instance_name}>
                  <ListRow
                    icon={<PuzzlePieceIcon />}
                    name={
                      definition
                        ? t(definition.display_name)
                        : plugin.plugin_key
                    }
                    secondary={`${plugin.instance_name} · ${plugin.plugin_key}`}
                    control={
                      <>
                        {missing && (
                          <Badge variant="warning">
                            {t("Unavailable in this deployment")}
                          </Badge>
                        )}
                        {!readOnly && changed && (
                          <Badge variant="info">
                            {original ? t("Modified") : t("Added")}
                          </Badge>
                        )}
                      </>
                    }
                    actions={
                      <>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          disabled={pending}
                          aria-label={t("Configure plugin {{name}}", {
                            name: plugin.instance_name,
                          })}
                          onClick={(event) =>
                            editor.activate(
                              { initial: plugin },
                              event.currentTarget,
                            )
                          }
                        >
                          {readOnly || missing
                            ? t("View configuration")
                            : t("Configure")}
                        </Button>
                        {!readOnly && (
                          <Menu>
                            <MenuTrigger
                              render={
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="icon-xs"
                                  disabled={pending}
                                  aria-label={t("Plugin actions")}
                                />
                              }
                            >
                              <DotsThreeIcon />
                            </MenuTrigger>
                            <MenuPopup>
                              <Confirm
                                title={t("Remove plugin")}
                                subject={plugin.instance_name}
                                description={t(
                                  "Remove this instance from the draft. Save the agent to apply the change.",
                                )}
                                danger
                                action={async () =>
                                  draft.removePlugin(plugin.instance_name)
                                }
                                onSuccess={() => {}}
                                triggerElement={
                                  <MenuItem
                                    variant="destructive"
                                    closeOnClick={false}
                                  >
                                    <TrashIcon />
                                    {t("Remove")}
                                  </MenuItem>
                                }
                              >
                                {!!groups.length && (
                                  <p>
                                    {t(
                                      "Also remove its references from these tool discovery groups: {{groups}}. Other sources are kept.",
                                      {
                                        groups: groups
                                          .map((group) => group.name)
                                          .join(", "),
                                      },
                                    )}
                                  </p>
                                )}
                              </Confirm>
                            </MenuPopup>
                          </Menu>
                        )}
                      </>
                    }
                  />
                  {missing && (
                    <p className="text-muted-foreground text-xs px-3 py-2">
                      {t(
                        "The saved configuration is kept. Ask an administrator to restore this plugin, or remove it before saving.",
                      )}
                    </p>
                  )}
                  {failureIndex === index && (
                    <p
                      role="alert"
                      className="text-destructive-foreground text-sm px-3 py-2"
                    >
                      {t(
                        "The configuration for {{name}} was rejected. Check its settings and whether the plugin is available, then save again.",
                        { name: plugin.instance_name },
                      )}
                    </p>
                  )}
                </div>
              );
            })}
          </ListRows>
        )}
        {!draft.plugins.length && !catalog.isPending && (
          <ListRowsEmpty>
            {!readOnly && available?.length === 0
              ? t(
                  "No plugins are enabled in this deployment. Ask an administrator to install and enable one.",
                )
              : t("No plugins added.")}
          </ListRowsEmpty>
        )}
        {!readOnly && available?.length === 0 && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void catalog.refetch()}
            loading={catalog.isFetching}
          >
            {t("Refresh plugins")}
          </Button>
        )}
        {!readOnly && removed.length > 0 && (
          <p className="text-muted-foreground text-xs">
            {t("Removed from draft: {{names}}", {
              names: removed.map((item) => item.instance_name).join(", "),
            })}
          </p>
        )}
      </Section>
      {editor.selected && (
        <PluginSettings
          key={`${editor.control.controlledOpen}:${editor.selected.initial?.instance_name ?? "new"}`}
          initial={editor.selected.initial}
          plugins={draft.plugins}
          catalog={available ?? []}
          readOnly={
            readOnly ||
            pending ||
            (!!editor.selected.initial &&
              !available?.some(
                (item) =>
                  item.plugin_key === editor.selected?.initial?.plugin_key,
              ))
          }
          control={editor.control}
          onApply={(plugin) =>
            draft.applyPlugin(plugin, editor.selected?.initial?.instance_name)
          }
        />
      )}
    </div>
  );
}
