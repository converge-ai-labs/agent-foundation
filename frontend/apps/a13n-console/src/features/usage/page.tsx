import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, SegmentedControl } from "a13n-ui";
import { useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { Page } from "../../shared/page";
import {
  RefreshButton,
  TimeRangePicker,
  type RangeSelection,
} from "../../shared/time-range";
import { exactCost, formatCost } from "../../shared/cost";
import { MemberPicker } from "./member-picker";
import { Breakdown, type UsageGroup } from "./breakdown";
import { UsageChart } from "./chart";
import { usagePage, useUsageView } from "./navigation";
import { SessionUsageSheet } from "./session-usage-sheet";
import { compactNumber, duration, percent, presetWindow } from "./values";
import styles from "./usage.module.css";

export function UsagePage() {
  const { workspace } = useWorkspace();
  return <UsageBrowser key={workspace.id} />;
}

function UsageBrowser() {
  const { t, i18n } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    { workspace, can } = useWorkspace();
  const admin = can("admin");
  const language = i18n.resolvedLanguage;
  const { view, update } = useUsageView(workspace.id, admin);
  const { metric, agent, group: selectedGroup, member: selectedMember } = view;
  const range: RangeSelection = useMemo(
    () =>
      "preset" in view.range
        ? view.range
        : {
            start: new Date(view.range.start),
            end: new Date(view.range.end),
          },
    [view.range],
  );
  // Keep the exact preset window when returning from a Session.
  const window = useMemo(
    () =>
      "preset" in range
        ? presetWindow(range.preset / 86400, new Date(view.now))
        : { start: range.start.toISOString(), end: range.end.toISOString() },
    [range, view.now],
  );
  function selectGroup(value: UsageGroup) {
    update({ group: value, agent: null, breakdownPages: [], sessionPages: [] });
  }
  const member = admin ? selectedMember : null;
  const group =
    !admin && selectedGroup === "members" ? "agents" : selectedGroup;
  const principalId = member?.principal_id;
  const chartGroup = group === "models" ? "models" : "agents";
  const toolbarRef = useRef<HTMLDivElement>(null);
  function selectMember(value: Schema["MemberUsage"] | null) {
    update({
      member: value
        ? {
            principal_id: value.principal_id,
            name: value.name,
            kind: value.kind,
            status: value.status,
          }
        : null,
      group: value && group === "members" ? "agents" : group,
      breakdownPages: [],
      sessionPages: [],
    });
  }
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const query = useQuery<Schema["UsageOverview"]>({
    queryKey: [
      "usage",
      workspace.id,
      "overview",
      chartGroup,
      metric,
      window,
      timezone,
      principalId,
    ],
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[5] === window &&
      previousQuery?.queryKey[7] === principalId
        ? previous
        : undefined,
    queryFn: async ({ signal }) =>
      data(
        await client.workspace(workspace.id).GET("/api/v1/usage/overview", {
          params: {
            query: {
              ...window,
              timezone,
              principal_id: principalId,
              group_by: chartGroup,
              metric,
            },
          },
          signal,
        }),
      ),
  });
  const fetching = useIsFetching({ queryKey: ["usage", workspace.id] });
  // Counts read compact at a glance; the exact value is in the title.
  const count = (value: number): [string, string] => [
    compactNumber(value, language),
    value.toLocaleString(language),
  ];
  const overview = query.data;
  const metrics: [string, string, string?][] = overview
    ? [
        [
          t("Spend"),
          formatCost(overview.usage.cost),
          exactCost(overview.usage.cost),
        ],
        [t("Runs"), ...count(overview.runs.runs)],
        [t("Model requests"), ...count(overview.usage.requests)],
        [
          t("Avg. run time"),
          duration(overview.runs.average_duration_seconds, language),
        ],
      ]
    : [];
  const tokens: [string, string, string?][] = overview
    ? [
        [t("Input tokens"), ...count(overview.usage.input_tokens)],
        [t("Output tokens"), ...count(overview.usage.output_tokens)],
        [t("Cached input tokens"), ...count(overview.usage.cache_read_tokens)],
        [t("Cache rate"), percent(overview.usage.cache_hit_rate)],
      ]
    : [];
  return (
    <Page
      title={t("Usage")}
      description={t("Model consumption and run time in this workspace.")}
      toolbar={
        <div className={styles.filters} ref={toolbarRef}>
          <div className={styles.filterControls}>
            <TimeRangePicker
              value={range}
              presets={usagePresets}
              maxDays={366}
              resolvePreset={(seconds) => {
                const preset = presetWindow(seconds / 86400);
                return {
                  start: new Date(preset.start),
                  end: new Date(preset.end),
                };
              }}
              onChange={(value) => {
                update({
                  range:
                    "preset" in value
                      ? value
                      : {
                          start: value.start.toISOString(),
                          end: value.end.toISOString(),
                        },
                  now: new Date().toISOString(),
                  breakdownPages: [],
                  sessionPages: [],
                });
              }}
            />
            {admin && (
              <MemberPicker
                window={window}
                member={member}
                onSelect={selectMember}
              />
            )}
          </div>
          <RefreshButton
            pending={fetching > 0}
            onRefresh={() => {
              if ("preset" in range)
                update({
                  now: new Date().toISOString(),
                  breakdownPages: [],
                  sessionPages: [],
                });
              else
                void cache.invalidateQueries({
                  queryKey: ["usage", workspace.id],
                });
            }}
          />
        </div>
      }
    >
      {member && (
        <div className={styles.memberScope} role="status">
          <div>
            <span>{t("Viewing usage for")}</span>
            <strong>{member.name}</strong>
            <span>
              {t(member.kind === "user" ? "User" : "Service account")}
              {member.status === "disabled" ? ` · ${t("Disabled")}` : ""}
            </span>
          </div>
          <Button variant="ghost" size="sm" onClick={() => selectMember(null)}>
            {t("Clear member filter")}
          </Button>
        </div>
      )}
      {query.isPending ? (
        <Loading variant="cards" />
      ) : query.error ? (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      ) : (
        overview && (
          <div className={styles.overview}>
            <dl className={styles.metrics}>
              {metrics.map(([name, value, exact]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd title={exact === value ? undefined : exact}>{value}</dd>
                </div>
              ))}
            </dl>
            <dl className={styles.tokens}>
              {tokens.map(([name, value, exact]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd title={exact === value ? undefined : exact}>{value}</dd>
                </div>
              ))}
            </dl>
            {overview.usage.unpriced_requests > 0 && (
              <p className={styles.notice} role="status">
                {t(
                  "{{count}} requests could not be priced. Spend shows the known model cost only.",
                  { count: overview.usage.unpriced_requests },
                )}
              </p>
            )}
            <UsageChart
              pending={query.isPlaceholderData}
              days={overview.daily}
              series={overview.series ?? []}
              metric={metric}
              onMetricChange={(metric) => update({ metric })}
              group={chartGroup}
              onGroupChange={selectGroup}
            />
          </div>
        )
      )}
      <section
        className={styles.breakdown}
        data-group={group}
        aria-labelledby="usage-breakdown-title"
      >
        <div className={styles.sectionHeading}>
          <h2 id="usage-breakdown-title">{t("Usage breakdown")}</h2>
          <SegmentedControl
            label={t("Group usage by")}
            value={group}
            onValueChange={(value) => {
              selectGroup(
                value === "members" && admin && !member
                  ? "members"
                  : value === "models"
                    ? "models"
                    : "agents",
              );
            }}
            options={[
              { value: "agents", label: t("By agent") },
              { value: "models", label: t("By model") },
              ...(admin && !member
                ? [{ value: "members", label: t("By member") }]
                : []),
            ]}
          />
        </div>
        {group === "members" && (
          <p className={styles.memberHint}>
            {t(
              "Shared bots are attributed to their service account. View a member’s usage to filter the entire page.",
            )}
          </p>
        )}
        <Breakdown
          key={`${group}:${principalId ?? "all"}`}
          window={window}
          group={group}
          page={usagePage(view.breakdownPages, (breakdownPages) =>
            update({ breakdownPages }),
          )}
          onAgentSelect={({ agent_id, name }) =>
            update({ agent: { agent_id, name }, sessionPages: [] })
          }
          principalId={principalId}
          onMemberSelect={(value) => {
            selectMember(value);
            toolbarRef.current?.scrollIntoView({ block: "start" });
          }}
        />
      </section>
      {agent && (
        <SessionUsageSheet
          agent={agent}
          window={window}
          member={member}
          page={usagePage(view.sessionPages, (sessionPages) =>
            update({ sessionPages }),
          )}
          onClose={() => update({ agent: null, sessionPages: [] })}
        />
      )}
      <p className={styles.footnote}>
        {t(
          "Usage is grouped by ingestion time. Runs are counted by start time; average time includes only sealed runs. Costs are in USD. Cached tokens are included in input tokens.",
        )}
      </p>
    </Page>
  );
}
const usagePresets = [7 * 86400, 30 * 86400];
