import { Fragment } from "react";
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

export function TokenDetails({ usage }: { usage?: ThreadUsage }) {
  if (!usage?.first_observed_at || !usage.root.model_requests)
    return (
      <p className={styles.scope}>No recorded root-agent token usage yet.</p>
    );
  const tokens = tokenCounts(usage.root);
  const rows = [
    [
      "Total tokens",
      (tokens.get("input_tokens") ?? 0) + (tokens.get("output_tokens") ?? 0),
    ],
    ["Input tokens", tokens.get("input_tokens") ?? 0],
    ["Output tokens", tokens.get("output_tokens") ?? 0],
    ["Cache read", tokens.get("cache_read_tokens") ?? 0],
    ["Cache write", tokens.get("cache_write_tokens") ?? 0],
    ["Model requests", usage.root.model_requests],
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
    <>
      <p className={styles.scope}>Root agent · cumulative recorded usage</p>
      <dl className={styles.numbers}>
        {rows.map(([label, value]) => (
          <Fragment key={label}>
            <dt>{label}</dt>
            <dd>{value.toLocaleString()}</dd>
          </Fragment>
        ))}
      </dl>
      <p className={styles.scope}>
        Cache and audio tokens are included in input/output. Subagents are
        excluded.
      </p>
    </>
  );
}

function CostValue({ totals }: { totals: ThreadUsage["root"] }) {
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

export function CostDetails({ usage }: { usage?: ThreadUsage }) {
  if (!usage?.first_observed_at || !usage.combined.model_requests)
    return <p className={styles.scope}>No recorded model costs yet.</p>;
  const models = [
    ...usage.models.flatMap(([name, totals]) =>
      typeof name === "string" && typeof totals === "object" && totals !== null
        ? [[name, totals] as const]
        : [],
    ),
    ...(usage.other_models.model_requests
      ? [["Other models", usage.other_models] as const]
      : []),
  ];
  return (
    <>
      <dl className={styles.numbers}>
        <dt>Root agent</dt>
        <dd>
          <CostValue totals={usage.root} />
        </dd>
        {usage.descendants.model_requests > 0 && (
          <>
            <dt>Subagents</dt>
            <dd>
              <CostValue totals={usage.descendants} />
            </dd>
            <dt>Combined</dt>
            <dd>
              <CostValue totals={usage.combined} />
            </dd>
          </>
        )}
      </dl>
      <h3 className={styles.sectionTitle}>By model</h3>
      <p className={styles.scope}>Root + subagents · USD</p>
      <ul className={styles.models}>
        {models.map(([name, totals]) => (
          <li key={name}>
            <div>
              <span className={styles.modelName}>{name}</span>
              <small className={styles.scope}>
                {totals.model_requests.toLocaleString()}{" "}
                {totals.model_requests === 1 ? "request" : "requests"}
              </small>
            </div>
            <div className={styles.modelCost}>
              <CostValue totals={totals} />
            </div>
          </li>
        ))}
      </ul>
      <p className={styles.scope}>
        Model estimates, not a provider invoice. + marks a partial known
        subtotal.
      </p>
    </>
  );
}
