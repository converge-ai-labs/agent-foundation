import { ArrowClockwiseIcon } from "@phosphor-icons/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, SegmentedControl } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { DateTimeField } from "../../shared/forms";
import { Page } from "../../shared/page";
import { formatCost } from "../../shared/cost";
import { Breakdown } from "./breakdown";
import { UsageChart } from "./chart";
import {
  customWindow,
  duration,
  localWindow,
  percent,
  presetWindow,
} from "./values";
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
  const [preset, setPreset] = useState("7");
  const [window, setWindow] = useState(() => presetWindow(7));
  const [draft, setDraft] = useState(() => localWindow(window));
  const [group, setGroup] = useState<"agents" | "models">("agents");
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const custom = customWindow(draft.start, draft.end);
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
  const number = (value: number) => value.toLocaleString(i18n.resolvedLanguage);
  const overview = query.data;
  const cards = overview
    ? [
        [t("Spend"), formatCost(overview.usage.cost)],
        [t("Runs"), number(overview.runs.runs)],
        [t("Model requests"), number(overview.usage.requests)],
        [t("Avg. run time"), duration(overview.runs.average_duration_seconds)],
        [t("Input tokens"), number(overview.usage.input_tokens)],
        [t("Output tokens"), number(overview.usage.output_tokens)],
        [t("Cached input tokens"), number(overview.usage.cache_read_tokens)],
        [t("Cache rate"), percent(overview.usage.cache_hit_rate)],
      ]
    : [];
  return (
    <Page
      title={t("Usage")}
      description={t("Model consumption and run time in this workspace.")}
      actions={
        <Button
          variant="outline"
          size="sm"
          disabled={query.isFetching}
          onClick={() => {
            if (preset !== "custom") {
              const next = presetWindow(Number(preset));
              setWindow(next);
              setDraft(localWindow(next));
            }
            void cache.invalidateQueries({ queryKey: ["usage", workspace.id] });
          }}
        >
          <ArrowClockwiseIcon aria-hidden="true" />
          {t("Refresh")}
        </Button>
      }
      toolbar={
        <div className={styles.filters}>
          <ChoiceField
            className="w-44"
            label={t("Date range")}
            hideLabel
            value={preset}
            options={[
              { value: "7", label: t("Last 7 days") },
              { value: "30", label: t("Last 30 days") },
              { value: "custom", label: t("Custom range") },
            ]}
            onValueChange={(value) => {
              setPreset(value);
              if (value !== "custom") {
                const next = presetWindow(Number(value));
                setWindow(next);
                setDraft(localWindow(next));
              }
            }}
          />
          <span className={styles.timezone}>{timezone}</span>
        </div>
      }
    >
      {preset === "custom" && (
        <form
          className={styles.customRange}
          onSubmit={(event) => {
            event.preventDefault();
            if (custom) setWindow(custom);
          }}
        >
          <DateTimeField
            label={t("From")}
            value={draft.start}
            onValueChange={(start) => setDraft({ ...draft, start })}
          />
          <DateTimeField
            label={t("Until")}
            value={draft.end}
            onValueChange={(end) => setDraft({ ...draft, end })}
          />
          <Button type="submit" variant="outline" disabled={!custom}>
            {t("Apply")}
          </Button>
          {!custom && (
            <p role="alert">
              {t("Choose a valid time range of up to 366 days.")}
            </p>
          )}
        </form>
      )}
      {query.isPending ? (
        <Loading variant="cards" />
      ) : query.error ? (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      ) : (
        overview && (
          <>
            <dl className={styles.metrics}>
              {cards.map(([name, value]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
            {overview.usage.unpriced_requests > 0 && (
              <p className={styles.notice} role="status">
                {t("{{count}} requests could not be priced.", {
                  count: overview.usage.unpriced_requests,
                })}{" "}
                {t("Spend shows the known model cost only.")}
              </p>
            )}
            <UsageChart days={overview.daily} />
          </>
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
          "Usage is grouped by ingestion time. Runs are counted by start time; average time includes only sealed runs.",
        )}{" "}
        {t("Costs are in USD. Cached tokens are included in input tokens.")}
      </p>
    </Page>
  );
}
