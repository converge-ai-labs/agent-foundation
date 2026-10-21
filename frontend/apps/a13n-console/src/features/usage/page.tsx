import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import { SegmentedControl } from "a13n-ui";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { Page } from "../../shared/page";
import {
  RefreshButton,
  TimeRangePicker,
  type RangeSelection,
} from "../../shared/time-range";
import { exactCost, formatCost } from "../../shared/cost";
import { Breakdown } from "./breakdown";
import { UsageChart } from "./chart";
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
    { workspace } = useWorkspace();
  const language = i18n.resolvedLanguage;
  const [range, setRange] = useState<RangeSelection>({ preset: 7 * 86400 });
  const [now, setNow] = useState(() => new Date());
  // Presets cover whole local days, ending now.
  const window = useMemo(
    () =>
      "preset" in range
        ? presetWindow(range.preset / 86400, now)
        : { start: range.start.toISOString(), end: range.end.toISOString() },
    [range, now],
  );
  const [group, setGroup] = useState<"agents" | "models">("agents");
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const query = useQuery({
    queryKey: ["usage", workspace.id, "overview", window, timezone],
    queryFn: async ({ signal }) =>
      data(
        await client.workspace(workspace.id).GET("/api/v1/usage/overview", {
          params: { query: { ...window, timezone } },
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
        <div className={styles.filters}>
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
              setRange(value);
              setNow(new Date());
            }}
          />
          <RefreshButton
            pending={fetching > 0}
            onRefresh={() => {
              if ("preset" in range) setNow(new Date());
              else
                void cache.invalidateQueries({
                  queryKey: ["usage", workspace.id],
                });
            }}
          />
        </div>
      }
    >
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
            <UsageChart days={overview.daily} />
          </div>
        )
      )}
      <section
        className={styles.breakdown}
        aria-labelledby="usage-breakdown-title"
      >
        <div className={styles.sectionHeading}>
          <h2 id="usage-breakdown-title">{t("Usage breakdown")}</h2>
          <SegmentedControl
            label={t("Group usage by")}
            value={group}
            onValueChange={(value) =>
              setGroup(value === "models" ? "models" : "agents")
            }
            options={[
              { value: "agents", label: t("By agent") },
              { value: "models", label: t("By model") },
            ]}
          />
        </div>
        <Breakdown window={window} group={group} />
      </section>
      <p className={styles.footnote}>
        {t(
          "Usage is grouped by ingestion time. Runs are counted by start time; average time includes only sealed runs. Costs are in USD. Cached tokens are included in input tokens.",
        )}
      </p>
    </Page>
  );
}
const usagePresets = [7 * 86400, 30 * 86400];
