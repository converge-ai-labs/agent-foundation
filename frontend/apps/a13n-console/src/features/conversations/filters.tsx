import {
  Button,
  Menu,
  MenuCheckboxItem,
  MenuPopup,
  MenuRadioGroup,
  MenuRadioItem,
  MenuTrigger,
  Popover,
  PopoverPopup,
  PopoverTrigger,
  SearchPicker,
} from "a13n-ui";
import { CaretDownIcon, XIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { SetURLSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { Toolbar } from "../../shared/collection";
import { DateTimeField } from "../../shared/forms";
import { ErrorNotice } from "../../shared/feedback";
import {
  formatLocalDateTime,
  parseLocalDateTime,
} from "../../shared/local-date-time";
import type { SessionFilters } from "./api";
import { zoneOffset } from "./format";
import styles from "./conversations.module.css";

const statuses: Schema["RunStatus"][] = [
  "accepted",
  "running",
  "waiting",
  "completed",
  "failed",
  "cancelled",
];
const triggers: Schema["Trigger"][] = [
  "input",
  "queued",
  "resume",
  "child_result",
  "spawned",
];
const keys = [
  "q",
  "application",
  "label",
  "agent_id",
  "status",
  "trigger",
  "updated_after",
  "updated_before",
];

export function readSessionFilters(search: URLSearchParams): SessionFilters {
  return {
    q: search.get("q")?.trim() || undefined,
    application: search.get("application") || undefined,
    label: search.getAll("label"),
    agent_id: search.get("agent_id") || undefined,
    status: known(search.getAll("status"), statuses),
    trigger: known(search.getAll("trigger"), triggers),
    updated_after: search.get("updated_after") || undefined,
    updated_before: search.get("updated_before") || undefined,
  };
}

/** URL values restricted to the filter's known options. */
function known<T extends string>(values: string[], options: T[]): T[] {
  return values.filter((value): value is T =>
    options.some((option) => option === value),
  );
}

/** The workspace's agents, shared by the Agent filter and the rows' avatars. */
export function useSessionAgents() {
  const { workspace, can } = useWorkspace();
  const client = useClient();
  return useQuery({
    queryKey: ["session-filter-agents", workspace.id],
    enabled: can("read"),
    staleTime: 60_000,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/agents", {
            params: {
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
}

export function SessionFilterBar({
  search,
  setSearch,
}: {
  search: URLSearchParams;
  setSearch: SetURLSearchParams;
}) {
  const { t } = useTranslation();
  const committedQuery = search.get("q") ?? "";
  const [query, setQuery] = useState(committedQuery);
  useEffect(() => setQuery(committedQuery), [committedQuery]);
  const agents = useSessionAgents();
  const update = (patch: Record<string, string[]>) =>
    setSearch((previous) => {
      const next = new URLSearchParams(previous);
      for (const [key, values] of Object.entries(patch)) {
        next.delete(key);
        for (const value of [...new Set(values)].sort())
          if (value) next.append(key, value);
      }
      return next;
    });
  // Typing narrows the server query, so it is committed once the reader pauses.
  const debounce = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(debounce.current), []);
  function onSearchChange(value: string) {
    setQuery(value);
    clearTimeout(debounce.current);
    debounce.current = setTimeout(
      () => update({ q: value.trim() ? [value.trim()] : [] }),
      300,
    );
  }
  const agentId = search.get("agent_id") ?? "";
  const agentOptions = (agents.data ?? []).map((agent) => ({
    value: agent.id,
    label: agent.name,
    keywords: [agent.id],
  }));
  if (agentId && !agentOptions.some((agent) => agent.value === agentId))
    agentOptions.push({ value: agentId, label: agentId, keywords: [] });
  const active = keys.some((key) => search.has(key));
  // Row sources and labels narrow the list; each stays visible until removed.
  const application = search.get("application");
  const labels = search.getAll("label");
  return (
    <>
      <Toolbar
        search={query}
        onSearchChange={onSearchChange}
        searchLabel={t("Search sessions")}
        searchPlaceholder={t("Title, source, ID, or message…")}
        filters={
          <>
            <div className={styles.agentFilter}>
              <SearchPicker
                label={t("Agent")}
                placeholder={t("Search agents")}
                emptyMessage={t("No matching agents")}
                // Names read in full, however narrow the trigger is.
                popupClassName={styles.agentFilterPopup}
                groups={[
                  {
                    label: t("Agents"),
                    options: [
                      { value: "all", label: t("All agents") },
                      ...agentOptions,
                    ],
                  },
                ]}
                value={agentId || "all"}
                onValueChange={(value) =>
                  update({ agent_id: value === "all" ? [] : [value] })
                }
              />
            </div>
            <MultiFilter
              label={t("Status")}
              values={search.getAll("status")}
              options={statuses.map((value) => ({
                value,
                label: t(`state.${value}`),
              }))}
              onChange={(values) => update({ status: values })}
            />
            <MultiFilter
              label={t("Trigger")}
              values={search.getAll("trigger")}
              options={triggers.map((value) => ({
                value,
                label: t(`trigger.${value}`),
              }))}
              onChange={(values) => update({ trigger: values })}
            />
            <UpdatedFilter
              after={search.get("updated_after")}
              before={search.get("updated_before")}
              onChange={(after, before) =>
                update({ updated_after: [after], updated_before: [before] })
              }
            />
            {active && (
              <Button
                variant="ghost"
                className={styles.clearFilters}
                onClick={() => {
                  setQuery("");
                  clearTimeout(debounce.current);
                  update(Object.fromEntries(keys.map((key) => [key, []])));
                }}
              >
                {t("Clear")}
              </Button>
            )}
          </>
        }
      />
      {(application || labels.length > 0) && (
        <div className={styles.activeFilters}>
          {application && (
            <FilterChip
              name={t("Source")}
              value={application}
              onRemove={() => update({ application: [] })}
            />
          )}
          {labels.map((label) => {
            const [key, ...value] = label.split(":");
            return (
              <FilterChip
                key={label}
                name={key}
                value={value.join(":")}
                onRemove={() =>
                  update({ label: labels.filter((item) => item !== label) })
                }
              />
            );
          })}
        </div>
      )}
      <ErrorNotice error={agents.error} retry={() => void agents.refetch()} />
    </>
  );
}

function FilterChip({
  name,
  value,
  onRemove,
}: {
  name: string;
  value: string;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  return (
    <span className={styles.activeFilter}>
      <span className={styles.activeFilterName}>{name}</span>
      <span className={styles.activeFilterValue}>{value}</span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`${t("Remove filter")} ${name}: ${value}`}
        title={t("Remove filter")}
      >
        <XIcon size={11} weight="bold" aria-hidden="true" />
      </button>
    </span>
  );
}

interface FilterOption {
  value: string;
  label: string;
}

/** A toolbar filter's face: what it filters, then what is chosen. */
function FilterTrigger({
  label,
  chosen,
}: {
  label: string;
  chosen?: ReactNode;
}) {
  return (
    <>
      <span className={styles.filterLabel}>{label}</span>
      {chosen}
      <CaretDownIcon size={12} aria-hidden="true" />
    </>
  );
}

/** Any number of values, chosen from a toolbar menu. */
export function MultiFilter({
  label,
  values,
  options,
  onChange,
}: {
  label: string;
  values: string[];
  options: FilterOption[];
  onChange: (values: string[]) => void;
}) {
  return (
    <Menu>
      <MenuTrigger render={<Button variant="outline" />}>
        <FilterTrigger
          label={label}
          chosen={
            values.length > 0 && (
              <span className={styles.filterCount}>{values.length}</span>
            )
          }
        />
      </MenuTrigger>
      <MenuPopup align="start" aria-label={label}>
        {options.map((option) => (
          <MenuCheckboxItem
            key={option.value}
            closeOnClick={false}
            checked={values.includes(option.value)}
            onCheckedChange={(checked) =>
              onChange(
                checked
                  ? [...values, option.value]
                  : values.filter((value) => value !== option.value),
              )
            }
          >
            {option.label}
          </MenuCheckboxItem>
        ))}
      </MenuPopup>
    </Menu>
  );
}

/** One value or all of them, for a filter the Service applies one value at a time. */
export function ChoiceFilter({
  label,
  allLabel,
  value,
  options,
  onChange,
}: {
  label: string;
  allLabel: string;
  value?: string;
  options: FilterOption[];
  onChange: (value: string | undefined) => void;
}) {
  const chosen = options.find((option) => option.value === value);
  return (
    <Menu>
      <MenuTrigger render={<Button variant="outline" />}>
        <FilterTrigger
          label={label}
          chosen={chosen && <span>{chosen.label}</span>}
        />
      </MenuTrigger>
      <MenuPopup align="start" aria-label={label}>
        <MenuRadioGroup
          value={chosen?.value ?? ""}
          onValueChange={(next: string) => onChange(next || undefined)}
        >
          <MenuRadioItem value="" closeOnClick>
            {allLabel}
          </MenuRadioItem>
          {options.map((option) => (
            <MenuRadioItem key={option.value} value={option.value} closeOnClick>
              {option.label}
            </MenuRadioItem>
          ))}
        </MenuRadioGroup>
      </MenuPopup>
    </Menu>
  );
}

function UpdatedFilter({
  after,
  before,
  onChange,
}: {
  after: string | null;
  before: string | null;
  onChange: (after: string, before: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const startDate = parseLocalDateTime(start),
    endDate = parseLocalDateTime(end);
  const invalid =
    (!!start && !startDate) ||
    (!!end && !endDate) ||
    (!!startDate && !!endDate && startDate >= endDate);
  const presets = [
    { days: 1, label: t("24 hours"), name: t("Last 24 hours") },
    { days: 7, label: t("7 days"), name: t("Last 7 days") },
    { days: 30, label: t("30 days"), name: t("Last 30 days") },
  ];
  return (
    <Popover
      open={open}
      onOpenChange={(value) => {
        if (value) {
          setStart(after ? formatLocalDateTime(new Date(after)) : "");
          setEnd(before ? formatLocalDateTime(new Date(before)) : "");
        }
        setOpen(value);
      }}
    >
      <PopoverTrigger render={<Button variant="outline" />}>
        <FilterTrigger
          label={t("Updated")}
          chosen={
            (after || before) && (
              <span
                className={styles.filterDot}
                aria-label={t("Filter active")}
              />
            )
          }
        />
      </PopoverTrigger>
      <PopoverPopup
        aria-label={t("Updated")}
        align="end"
        className={styles.updatedPopup}
      >
        <div className={styles.updatedBody}>
          <div className={styles.presets}>
            {presets.map((preset) => (
              <Button
                key={preset.days}
                variant="ghost"
                size="sm"
                aria-label={preset.name}
                onClick={() => {
                  const now = new Date();
                  setStart(
                    formatLocalDateTime(
                      new Date(now.getTime() - preset.days * 86400000),
                    ),
                  );
                  setEnd(formatLocalDateTime(now));
                }}
              >
                {preset.label}
              </Button>
            ))}
          </div>
          <div className={styles.updatedFields}>
            <DateTimeField
              label={t("Start time")}
              value={start}
              onValueChange={setStart}
            />
            <DateTimeField
              label={t("End time")}
              value={end}
              onValueChange={setEnd}
            />
          </div>
          {invalid && (
            <p role="alert" className={styles.updatedError}>
              {t("Choose a valid time range.")}
            </p>
          )}
          <div className={styles.updatedFooter}>
            {/* The fields read in the browser's zone; its full name is a hover away. */}
            <span title={Intl.DateTimeFormat().resolvedOptions().timeZone}>
              {zoneOffset(undefined, i18n.resolvedLanguage)}
            </span>
            <div className={styles.updatedActions}>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setStart("");
                  setEnd("");
                }}
              >
                {t("Any time")}
              </Button>
              <Button
                size="sm"
                disabled={invalid}
                onClick={() => {
                  onChange(
                    startDate?.toISOString() ?? "",
                    endDate?.toISOString() ?? "",
                  );
                  setOpen(false);
                }}
              >
                {t("Apply")}
              </Button>
            </div>
          </div>
        </div>
      </PopoverPopup>
    </Popover>
  );
}
