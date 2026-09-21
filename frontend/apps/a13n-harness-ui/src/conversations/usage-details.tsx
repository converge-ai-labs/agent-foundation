import { Fragment, useState } from "react";
import { Button } from "a13n-ui";
import { modelCost, tokenCounts, type ThreadUsage } from "./usage";
import styles from "./composer-status.module.css";

export function ContextDetails({
  used,
  capacity,
}: {
  used?: number | null;
  capacity?: number | null;
}) {
  const limit = capacity != null && capacity > 0 ? capacity : undefined;
  const remaining =
    used != null && limit != null ? Math.max(0, limit - used) : undefined;
  return (
    <>
      <dl className={styles.numbers}>
        <dt>Used tokens</dt>
        <dd>{used?.toLocaleString() ?? "Unknown"}</dd>
        <dt>Context window</dt>
        <dd>{limit?.toLocaleString() ?? "Unknown"}</dd>
        <dt>Remaining tokens</dt>
        <dd>{remaining?.toLocaleString() ?? "Unknown"}</dd>
        {used != null && limit != null && used > limit && (
          <>
            <dt>Over capacity</dt>
            <dd>{(used - limit).toLocaleString()}</dd>
          </>
        )}
      </dl>
      <p className={styles.scope}>Latest root request, not cumulative usage.</p>
    </>
  );
}

type Totals = ThreadUsage["root"];
type Scope = "combined" | "root" | "descendants";

function CostValue({ totals }: { totals: Totals }) {
  return (
    <>
      <strong>{modelCost(totals)}</strong>
      {totals.unknown_model_costs > 0 && (
        <small className={styles.scope}>
          {totals.unknown_model_costs.toLocaleString()} unknown-cost{" "}
          {totals.unknown_model_costs === 1 ? "response" : "responses"}
        </small>
      )}
    </>
  );
}

function TokenRows({ totals }: { totals: Totals }) {
  const tokens = tokenCounts(totals);
  const rows = [
    ["Input tokens", tokens.get("input_tokens") ?? 0],
    ["Output tokens", tokens.get("output_tokens") ?? 0],
    ["Cache read", tokens.get("cache_read_tokens") ?? 0],
    ["Cache write", tokens.get("cache_write_tokens") ?? 0],
    ["Model requests", totals.model_requests],
    ...(
      [
        ["Input audio", "input_audio_tokens"],
        ["Output audio", "output_audio_tokens"],
        ["Cached audio", "cache_audio_read_tokens"],
      ] as const
    ).flatMap(([label, key]) =>
      tokens.get(key) ? [[label, tokens.get(key)!] as const] : [],
    ),
  ] as const;
  return (
    <dl className={styles.numbers}>
      {rows.map(([label, value]) => (
        <Fragment key={label}>
          <dt>{label}</dt>
          <dd>{value.toLocaleString()}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

export function UsageDetails({ usage }: { usage?: ThreadUsage }) {
  const [scope, setScope] = useState<Scope>("combined");
  if (!usage?.first_observed_at)
    return (
      <p className={styles.scope}>
        No recorded usage yet. Historical coverage is unavailable, not zero.
      </p>
    );
  const totals = usage[scope];
  const tokens = tokenCounts(totals);
  const scopes = usage.model_scopes ?? [];
  const models: (readonly [string, Totals])[] = scopes.length
    ? scopes
        .filter((model) => model[scope].model_requests > 0)
        .map((model) => [model.name, model[scope]] as const)
    : scope === "combined"
      ? [
          ...usage.models.flatMap(([name, value]) =>
            typeof name === "string" &&
            typeof value === "object" &&
            value !== null
              ? [[name, value] as const]
              : [],
          ),
          ...(usage.other_models.model_requests
            ? [["Other models", usage.other_models] as const]
            : []),
        ]
      : [];
  return (
    <div className={styles.usageDetails}>
      <div
        className={styles.scopeSelector}
        role="group"
        aria-label="Usage scope"
      >
        {(
          [
            ["combined", "All"],
            ["root", "Root"],
            ["descendants", "Subagents"],
          ] as const
        ).map(([value, label]) => (
          <Button
            key={value}
            variant="ghost"
            size="sm"
            aria-pressed={scope === value}
            onClick={() => setScope(value)}
          >
            {label}
          </Button>
        ))}
      </div>
      <p className={styles.scope}>
        Recorded conversation usage · auxiliary models included
      </p>
      <dl className={styles.numbers}>
        <dt>Total tokens</dt>
        <dd>
          {totals.model_requests
            ? (
                (tokens.get("input_tokens") ?? 0) +
                (tokens.get("output_tokens") ?? 0)
              ).toLocaleString()
            : "—"}
        </dd>
        <dt>Model cost · USD</dt>
        <dd>
          <CostValue totals={totals} />
        </dd>
      </dl>
      <TokenRows totals={totals} />
      <h3 className={styles.sectionTitle}>By model</h3>
      {!models.length && (
        <p className={styles.scope}>
          {totals.model_requests
            ? "Model attribution is unavailable for this scope in older data."
            : "No recorded model responses in this scope."}
        </p>
      )}
      <ul className={styles.models}>
        {models.map(([name, value]) => {
          const groups = (usage.groups ?? []).filter(
            (group) =>
              group.model === name &&
              (scope === "combined" ||
                group.descendant === (scope === "descendants")),
          );
          return (
            <li key={name}>
              <div className={styles.modelHeader}>
                <span className={styles.modelName}>{name}</span>
                <div className={styles.modelCost}>
                  <CostValue totals={value} />
                </div>
              </div>
              <TokenRows totals={value} />
              {groups.length > 0 && (
                <details className={styles.attribution}>
                  <summary>Agents and sources</summary>
                  {groups.map((group) => (
                    <div
                      className={styles.sourceGroup}
                      key={`${group.agent_instance_id}:${group.descendant}:${group.source}`}
                    >
                      <div>
                        {group.descendant ? "Subagent" : "Root"} ·{" "}
                        {group.source === "agent"
                          ? "Agent requests"
                          : group.source === "files.media_understanding"
                            ? "Media understanding · view"
                            : group.source || "Unattributed"}
                      </div>
                      <small className={styles.scope}>
                        {group.agent_instance_id || "Unknown agent"}
                      </small>
                      <div>
                        {group.totals.model_requests.toLocaleString()} requests
                        · <CostValue totals={group.totals} />
                      </div>
                      <TokenRows totals={group.totals} />
                    </div>
                  ))}
                </details>
              )}
            </li>
          );
        })}
      </ul>
      {!!usage.other_groups?.model_requests && (
        <p className={styles.scope}>
          Some agent/source details are omitted. All contributions remain
          included in the model and scope totals.
        </p>
      )}
      {totals.provider_receipts > 0 && (
        <details className={styles.attribution}>
          <summary>
            Non-model providers · {totals.provider_receipts.toLocaleString()}{" "}
            receipts
          </summary>
          <p className={styles.scope}>
            Separate from model costs; currencies are not converted.
          </p>
          <dl className={styles.numbers}>
            {totals.provider_costs.map(([currency, cost]) => (
              <Fragment key={String(currency)}>
                <dt>{String(currency)}</dt>
                <dd>{String(cost)}</dd>
              </Fragment>
            ))}
          </dl>
          {!!totals.unknown_provider_costs && (
            <p className={styles.scope}>
              {totals.unknown_provider_costs} unknown-cost receipts
            </p>
          )}
          {!!totals.omitted_currency_receipts && (
            <p className={styles.scope}>
              {totals.omitted_currency_receipts} currency entries omitted
            </p>
          )}
        </details>
      )}
      <p className={styles.scope}>
        Cache/audio counters are included in input/output, not additional
        tokens. Agent and source details are breakdowns, not extra usage.
      </p>
      <p className={styles.scope}>
        Model estimates, not a provider invoice. + marks a partial known
        subtotal. Missing historical prices are not backfilled.
      </p>
    </div>
  );
}

export const TokenDetails = UsageDetails;
export const CostDetails = UsageDetails;
