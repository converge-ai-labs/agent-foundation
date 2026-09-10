import {
  Button,
  FormField,
  Input,
  Menu,
  MenuCheckboxItem,
  MenuPopup,
  MenuTrigger,
  Popover,
  PopoverPopup,
  PopoverTrigger,
  SearchPicker,
} from "a13n-ui";
import { CaretDownIcon, MagnifyingGlassIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { SetURLSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { DateTimeField } from "../../shared/date-time-field";
import { ErrorNotice } from "../../shared/feedback";
import {
  formatLocalDateTime,
  parseLocalDateTime,
} from "../../shared/local-date-time";
import type { SessionFilters } from "./api";

const statuses: Schema["RunStatus"][] = [
  "accepted",
  "running",
  "waiting",
  "completed",
  "failed",
  "cancelled",
];
const triggers = [
  "user_input",
  "feedback",
  "queued_submission",
  "inbound",
  "async_subagent",
  "async_subagent_resume",
  "async_subagent_result",
];
const keys = [
  "q",
  "agent_id",
  "status",
  "trigger_type",
  "updated_after",
  "updated_before",
];

export function readSessionFilters(search: URLSearchParams): SessionFilters {
  return {
    q: search.get("q")?.trim() || undefined,
    agent_id: search.get("agent_id") || undefined,
    status: search.getAll("status") as Schema["RunStatus"][],
    trigger_type: search.getAll("trigger_type"),
    updated_after: search.get("updated_after") || undefined,
    updated_before: search.get("updated_before") || undefined,
  };
}

export function SessionFilterBar({
  search,
  setSearch,
}: {
  search: URLSearchParams;
  setSearch: SetURLSearchParams;
}) {
  const { t } = useTranslation();
  const { workspace, can } = useWorkspace();
  const client = useClient();
  const [query, setQuery] = useState(search.get("q") ?? "");
  const committedQuery = search.get("q") ?? "";
  useEffect(() => setQuery(committedQuery), [committedQuery]);
  const agents = useQuery({
    queryKey: ["session-filter-agents", workspace.id],
    enabled: can("agent.read"),
    staleTime: 60_000,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace}/agents", {
            params: {
              path: { workspace: workspace.id },
              query: { cursor, limit: 100, include_archived: true },
            },
            signal,
          })
          .then(data),
      ),
  });
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
  const agentId = search.get("agent_id") ?? "";
  const agentOptions = (agents.data ?? []).map((agent) => ({
    value: agent.id,
    label: agent.name,
    keywords: [agent.id],
  }));
  if (agentId && !agentOptions.some((agent) => agent.value === agentId))
    agentOptions.push({ value: agentId, label: agentId, keywords: [] });
  return (
    <>
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <form
          className="flex min-w-64 flex-1 items-center gap-1"
          onSubmit={(event) => {
            event.preventDefault();
            update({ q: [query.trim()] });
          }}
        >
          <FormField
            label={t("Search sessions")}
            hideLabel
            className="min-w-0 flex-1"
          >
            <Input
              type="search"
              maxLength={72}
              placeholder={t("Session ID or Thread ID…")}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                if (!event.target.value) update({ q: [] });
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && event.nativeEvent.isComposing)
                  event.preventDefault();
              }}
            />
          </FormField>
          <Button
            type="submit"
            variant="outline"
            size="icon"
            aria-label={t("Search")}
          >
            <MagnifyingGlassIcon />
          </Button>
        </form>
        <div className="w-44">
          <SearchPicker
            label={t("Recent agent")}
            placeholder={t("All agents")}
            emptyMessage={t("No matching agents")}
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
          label={t("Run status")}
          values={search.getAll("status")}
          options={statuses.map((value) => ({
            value,
            label: t(`state.${value}`),
          }))}
          onChange={(values) => update({ status: values })}
        />
        <MultiFilter
          label={t("Trigger source")}
          values={search.getAll("trigger_type")}
          options={triggers.map((value) => ({
            value,
            label: t(`trigger.${value}`),
          }))}
          onChange={(values) => update({ trigger_type: values })}
        />
        <UpdatedFilter
          after={search.get("updated_after")}
          before={search.get("updated_before")}
          onChange={(after, before) =>
            update({ updated_after: [after], updated_before: [before] })
          }
        />
        {keys.some((key) => search.has(key)) && (
          <Button
            variant="ghost"
            onClick={() => {
              setQuery("");
              update(Object.fromEntries(keys.map((key) => [key, []])));
            }}
          >
            {t("Clear filters")}
          </Button>
        )}
      </div>
      <ErrorNotice error={agents.error} retry={() => void agents.refetch()} />
    </>
  );
}

function MultiFilter({
  label,
  values,
  options,
  onChange,
}: {
  label: string;
  values: string[];
  options: { value: string; label: string }[];
  onChange: (values: string[]) => void;
}) {
  return (
    <Menu>
      <MenuTrigger render={<Button variant="outline" />}>
        {label}
        {values.length > 0 && (
          <span className="text-muted-foreground">{values.length}</span>
        )}
        <CaretDownIcon />
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

function UpdatedFilter({
  after,
  before,
  onChange,
}: {
  after: string | null;
  before: string | null;
  onChange: (after: string, before: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const startDate = parseLocalDateTime(start),
    endDate = parseLocalDateTime(end);
  const invalid =
    (!!start && !startDate) ||
    (!!end && !endDate) ||
    (!!startDate && !!endDate && startDate >= endDate);
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
        {t("Last updated")}
        {(after || before) && (
          <span
            aria-label={t("Filter active")}
            className="size-1.5 rounded-full bg-current"
          />
        )}
        <CaretDownIcon />
      </PopoverTrigger>
      <PopoverPopup
        aria-label={t("Last updated")}
        align="end"
        className="w-[22rem] max-w-[calc(100vw-2rem)]"
      >
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-3">
            <p className="text-sm font-medium">{t("Time range")}</p>
            <div className="grid grid-cols-3 gap-1 rounded-lg bg-muted/60 p-1">
              {[1, 7, 30].map((days) => (
                <Button
                  key={days}
                  variant="ghost"
                  size="sm"
                  className="font-normal"
                  aria-label={t(
                    days === 1
                      ? "Last 24 hours"
                      : days === 7
                        ? "Last 7 days"
                        : "Last 30 days",
                  )}
                  onClick={() => {
                    const now = new Date();
                    setStart(
                      formatLocalDateTime(
                        new Date(now.getTime() - days * 86400000),
                      ),
                    );
                    setEnd(formatLocalDateTime(now));
                  }}
                >
                  {t(
                    days === 1 ? "24 hours" : days === 7 ? "7 days" : "30 days",
                  )}
                </Button>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-4 [&_[data-slot=popover-trigger]]:w-full [&_[data-slot=field-label]]:text-xs [&_[data-slot=field-label]]:text-muted-foreground">
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
            <p role="alert" className="text-sm text-destructive">
              {t("Choose a valid time range.")}
            </p>
          )}
          <div className="flex items-center justify-between gap-2 border-t pt-3">
            <span className="text-xs text-muted-foreground">
              {Intl.DateTimeFormat().resolvedOptions().timeZone}
            </span>
            <div className="flex items-center gap-2">
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
