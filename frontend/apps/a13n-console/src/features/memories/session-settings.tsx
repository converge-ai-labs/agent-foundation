import { BrainIcon, XIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  Input,
  Label,
  Switch,
  Tooltip,
  TooltipTrigger,
  TooltipPopup,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { spaceQueries } from "./spaces-api";
import { validSpaceSelection } from "./space-form";
export { validSpaceSelection, defaultSessionMemory } from "./space-form";
import { ListRowsEmpty, ResourcePicker } from "../../shared/collection";
import { ErrorNotice, StatePill } from "../../shared/feedback";
import styles from "./space-selection.module.css";

type Settings = Schema["SessionMemorySettings"];

export function SessionMemoryFields({
  value,
  onChange,
  disabled = false,
  compact = false,
}: {
  value: Settings;
  onChange: (value: Settings, field: keyof Settings) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace, basePath } = useWorkspace();
  const query = useQuery(spaceQueries(client, workspace.id).all());
  const bindings = value.spaces ?? [];
  const selected = new Set(bindings.map((item) => item.space_id));
  return (
    <div
      data-slot="session-memory-fields"
      className={`${styles.selection} ${compact ? styles.compact : ""}`}
    >
      <div className={styles.participation}>
        <div>
          <Label htmlFor="memory-participation">
            {t(compact ? "Memory spaces" : "Use memory spaces")}
          </Label>
          <p>
            {t(
              compact
                ? "Share knowledge across sessions."
                : "Applies to new runs. Turning off keeps selected spaces and saved files.",
            )}
          </p>
        </div>
        <Switch
          id="memory-participation"
          checked={!!value.enabled}
          disabled={disabled}
          onCheckedChange={(enabled) =>
            onChange({ ...value, enabled }, "enabled")
          }
        />
      </div>
      {(!compact || value.enabled) && (
        <div className={styles.spaces}>
          <div className={styles.spacesHeader}>
            <div>
              <h3>{t("Selected spaces")}</h3>
              {compact && !bindings.length && <p>{t("No spaces selected")}</p>}
              {!compact && (
                <p>
                  {t(
                    "Shared knowledge and this session's notes, in each space.",
                  )}
                </p>
              )}
            </div>
            {!disabled && (
              <ResourcePicker
                label={t("Add space")}
                searchLabel={t("Search spaces")}
                emptyLabel={t("No memory spaces yet")}
                loading={query.isPending}
                manageHref={`${basePath}/memory-spaces`}
                manageLabel={t("Manage spaces")}
                selected={selected}
                items={(query.data ?? []).map((space) => ({
                  id: space.id,
                  name: space.name,
                  detail: space.description ?? undefined,
                  icon: !space.enabled ? (
                    <StatePill state="disabled" />
                  ) : undefined,
                }))}
                onToggle={(id, checked) => {
                  if (!checked) {
                    onChange(
                      {
                        ...value,
                        spaces: bindings.filter((item) => item.space_id !== id),
                      },
                      "spaces",
                    );
                    return;
                  }
                  let index = bindings.length + 1;
                  while (
                    bindings.some((item) => item.name === `space-${index}`)
                  )
                    index++;
                  onChange(
                    {
                      ...value,
                      spaces: [
                        ...bindings,
                        {
                          name: `space-${index}`,
                          space_id: id,
                          access: "write",
                        },
                      ],
                    },
                    "spaces",
                  );
                }}
              />
            )}
          </div>
          <ErrorNotice error={query.error} retry={() => void query.refetch()} />
          {bindings.length ? (
            <div className={styles.rows}>
              {bindings.map((binding, index) => {
                const space = query.data?.find(
                  (item) => item.id === binding.space_id,
                );
                return (
                  <div className={styles.row} key={binding.space_id}>
                    <div className={styles.identity}>
                      <BrainIcon size={18} />
                      <Link
                        to={`${basePath}/memory-spaces/${binding.space_id}`}
                      >
                        {space?.name ?? binding.space_id}
                      </Link>
                      {space && !space.enabled && (
                        <StatePill state="disabled" />
                      )}
                    </div>
                    <div className={styles.controls}>
                      <Label className={styles.alias}>
                        {t("Mount name")}
                        <Input
                          value={binding.name}
                          disabled={disabled}
                          required
                          pattern="[a-z][a-z0-9-]{0,62}"
                          onChange={(event) =>
                            onChange(
                              {
                                ...value,
                                spaces: bindings.map((item, i) =>
                                  i === index
                                    ? { ...item, name: event.target.value }
                                    : item,
                                ),
                              },
                              "spaces",
                            )
                          }
                        />
                      </Label>
                      <ChoiceField
                        label={t("Access")}
                        value={binding.access ?? "write"}
                        disabled={disabled}
                        options={[
                          { value: "read", label: t("Read only") },
                          { value: "write", label: t("Read and write") },
                        ]}
                        onValueChange={(access) =>
                          onChange(
                            {
                              ...value,
                              spaces: bindings.map((item, i) =>
                                i === index
                                  ? {
                                      ...item,
                                      access: access as "read" | "write",
                                    }
                                  : item,
                              ),
                            },
                            "spaces",
                          )
                        }
                      />
                      {!disabled && (
                        <Tooltip>
                          <TooltipTrigger
                            render={
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon-sm"
                                aria-label={t("Remove space")}
                                onClick={() =>
                                  onChange(
                                    {
                                      ...value,
                                      spaces: bindings.filter(
                                        (_, i) => i !== index,
                                      ),
                                    },
                                    "spaces",
                                  )
                                }
                              />
                            }
                          >
                            <XIcon size={14} />
                          </TooltipTrigger>
                          <TooltipPopup>{t("Remove space")}</TooltipPopup>
                        </Tooltip>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : compact ? null : (
            <ListRowsEmpty>
              {t(
                "No spaces selected. No memory will be created automatically.",
              )}
            </ListRowsEmpty>
          )}
          {!validSpaceSelection(value) && (
            <p role="alert" className="text-destructive text-xs">
              {t(
                "Use unique mount names: lowercase letters, digits and hyphens, starting with a letter.",
              )}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
