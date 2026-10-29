import { HelpTip } from "../shell/help-tip";
import { ModelIdentity } from "../shell/model-identity";
import { Fragment, useState } from "react";
import { Button } from "a13n-ui";
import { ArrowRight } from "@phosphor-icons/react";
import { modelCost, tokenCounts, type ThreadUsage } from "./usage";
import styles from "./composer-status.module.css";
import { DebugOnly } from "../shell/debug";

export function ContextDetails({
  used,
  capacity,
  compact = false,
  cacheRate,
}: {
  used?: number | null;
  capacity?: number | null;
  compact?: boolean;
  cacheRate?: string;
}) {
  const limit = capacity != null && capacity > 0 ? capacity : undefined;
  const remaining =
    used != null && limit != null ? Math.max(0, limit - used) : undefined;
  const percent =
    used != null && limit != null ? (used / limit) * 100 : undefined;
  return (
    <>
      {compact && (
        <div className={styles.contextHero}>
          <div>
            <strong>{used?.toLocaleString() ?? "—"}</strong>
            <span>tokens used</span>
          </div>
          {percent != null && (
            <span className={styles.contextPercent}>
              {Math.round(percent)}%
            </span>
          )}
        </div>
      )}
      {percent != null && (
        <div className={styles.contextCapacity}>
          {!compact && <span>{Math.round(percent)}% used</span>}
          <div
            className={styles.contextMeter}
            role="meter"
            aria-label="Context used"
            aria-valuemin={0}
            aria-valuemax={limit}
            aria-valuenow={Math.min(used!, limit!)}
            aria-valuetext={`${used!.toLocaleString()} of ${limit!.toLocaleString()} tokens`}
            data-full={percent >= 90 || undefined}
          >
            <span style={{ width: `${Math.min(100, percent)}%` }} />
          </div>
        </div>
      )}
      <dl
        className={`${styles.numbers} ${styles.contextNumbers}`}
        data-unknown-limit={limit == null || undefined}
      >
        {!compact && (
          <>
            <dt>Used tokens</dt>
            <dd>{used?.toLocaleString() ?? "Unknown"}</dd>
          </>
        )}
        {limit != null && (
          <>
            <dt>Context window</dt>
            <dd>{limit.toLocaleString()}</dd>
            <dt>Remaining tokens</dt>
            <dd>{remaining?.toLocaleString() ?? "Unknown"}</dd>
          </>
        )}
        {used != null && limit != null && used > limit && (
          <>
            <dt>Over capacity</dt>
            <dd>{(used - limit).toLocaleString()}</dd>
          </>
        )}
        {cacheRate != null && !compact && (
          <>
            <dt>Cache rate</dt>
            <dd>{cacheRate}</dd>
          </>
        )}
      </dl>
      {compact && cacheRate != null && (
        <dl className={styles.contextCache}>
          <dt>Cache rate</dt>
          <dd>{cacheRate}</dd>
        </dl>
      )}
      {limit == null && !compact && (
        <p className={styles.scope}>Context limit unavailable.</p>
      )}
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
        <HelpTip label="About estimated cost">
          {totals.unknown_model_costs.toLocaleString()}{" "}
          {totals.unknown_model_costs === 1 ? "response has" : "responses have"}{" "}
          no recorded price and{" "}
          {totals.unknown_model_costs === 1 ? "is" : "are"} excluded from this
          estimate.
        </HelpTip>
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
        No usage recorded yet. Earlier usage may be missing.
      </p>
    );
  const totals = usage[scope];
  const tokens = tokenCounts(totals);
  const input = tokens.get("input_tokens") ?? 0;
  const output = tokens.get("output_tokens") ?? 0;
  const total = input + output;
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
            ["root", "This chat"],
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
      {totals.model_requests > 0 && (
        <dl className={styles.usageOverview}>
          <dt>Total tokens</dt>
          <dd>{totals.model_requests ? total.toLocaleString() : "—"}</dd>
          <dt>Model cost · USD</dt>
          <dd>
            <CostValue totals={totals} />
          </dd>
        </dl>
      )}
      {total > 0 && (
        <div className={styles.tokenDistribution}>
          <div className={styles.tokenBar} aria-hidden="true">
            <span style={{ width: `${(input / total) * 100}%` }} />
          </div>
          <div className={styles.tokenLegend}>
            <span>
              <i />
              Input <strong>{input.toLocaleString()}</strong>
            </span>
            <span>
              <i />
              Output <strong>{output.toLocaleString()}</strong>
            </span>
          </div>
        </div>
      )}
      {totals.model_requests > 0 ? (
        <>
          <details className={styles.breakdown}>
            <summary>
              Token breakdown{" "}
              <span>
                {totals.model_requests.toLocaleString()}{" "}
                {totals.model_requests === 1 ? "request" : "requests"}
              </span>
            </summary>
            <TokenRows totals={totals} />
          </details>
          <details className={styles.breakdown}>
            <summary>
              By model <span>{models.length}</span>
            </summary>
            {!models.length && (
              <p className={styles.scope}>
                Older records don't show which models were used here.
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
                    <details className={styles.modelDetails}>
                      <summary className={styles.modelHeader}>
                        <span className={styles.modelName} title={name}>
                          <ModelIdentity name={name} />
                        </span>
                        <span className={styles.modelCost}>
                          {modelCost(value)}
                        </span>
                      </summary>
                      <TokenRows totals={value} />
                      {value.unknown_model_costs > 0 && (
                        <p className={styles.scope}>
                          {value.unknown_model_costs} responses have no recorded
                          price.
                        </p>
                      )}
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
                              <DebugOnly>
                                <small className={styles.scope}>
                                  {group.agent_instance_id || "Unknown agent"}
                                </small>
                              </DebugOnly>
                              <div>
                                {group.totals.model_requests.toLocaleString()}{" "}
                                requests · <CostValue totals={group.totals} />
                              </div>
                              <TokenRows totals={group.totals} />
                            </div>
                          ))}
                        </details>
                      )}
                    </details>
                  </li>
                );
              })}
            </ul>
            {!!usage.other_groups?.model_requests && (
              <p className={styles.scope}>
                Some breakdown details are hidden. Their usage is still included
                in the totals.
              </p>
            )}
          </details>
        </>
      ) : (
        <p className={styles.emptyScope}>No usage in this scope yet.</p>
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
      <details className={styles.breakdown}>
        <summary>About these numbers</summary>
        <p className={styles.scope}>
          Includes auxiliary models and recorded usage in this chat.
        </p>
        <p className={styles.scope}>
          Cache and audio tokens are already included in input and output. Agent
          and source totals are breakdowns of the same usage.
        </p>
        <p className={styles.scope}>
          Estimated cost, not a bill. + means some prices are missing. Older
          missing prices stay unknown.
        </p>
      </details>
      <a
        className={styles.analyticsLink}
        href={`/usage?thread_id=${encodeURIComponent(usage.thread_id)}`}
      >
        Open analytics <ArrowRight aria-hidden size={14} />
      </a>
    </div>
  );
}

export const TokenDetails = UsageDetails;
export const CostDetails = UsageDetails;
