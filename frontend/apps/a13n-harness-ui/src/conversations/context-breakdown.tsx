import { useState } from "react";
import { Collapsible, CollapsiblePanel, CollapsibleTrigger } from "a13n-ui";
import { CaretRight } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import type { Schema } from "../transport/client";
import styles from "./context-breakdown.module.css";

const sections = {
  system: ["System prompt", "var(--a13n-brand, #7953ed)"],
  tools: ["Tool definitions", "#598eef"],
  skills: ["Skills", "#54c69b"],
  memory: ["Memory", "#b079dc"],
  environment_context: ["Environment context", "#f39b57"],
  runtime_state: ["Working state", "#ed85af"],
  conversation: ["Conversation", "#7a88ef"],
  tool_calls: ["Tool calls", "#32bcbc"],
  other: ["Other", "#929bab"],
} as const satisfies Record<
  Schema<"ContextGroupName">,
  readonly [string, string]
>;

function GroupDetails({
  group,
  value,
  format,
  share,
  color,
}: {
  color: string;
  group: string;
  value: Schema<"ContextGroupUsage">;
  format: (chars: number) => string;
  share: (chars: number) => string;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const items = Object.entries(value.items ?? {}).sort(
    ([a, x], [b, y]) => y.characters - x.characters || a.localeCompare(b),
  );
  const balance =
    value.characters - items.reduce((n, [, v]) => n + v.characters, 0);
  const renderItem = ([name, item]: (typeof items)[number]) => (
    <div className={styles.item} key={name}>
      <span className={styles.itemName}>{name}</span>
      <span>{format(item.characters)}</span>
      <span className={styles.share}>{share(item.characters)}</span>
      <span
        aria-hidden="true"
        className={styles.itemBar}
        style={{
          width: `${(100 * item.characters) / (value.characters || 1)}%`,
          background: color,
        }}
      />
      {item.description && <small>{item.description}</small>}
      {item.calls != null && (
        <small>
          {t(
            "{{calls}} calls · {{args, number}} argument chars · {{results, number}} result chars",
            {
              calls: item.calls,
              args: item.argument_characters ?? 0,
              results: item.result_characters ?? 0,
            },
          )}
        </small>
      )}
    </div>
  );
  return (
    <div className={styles.detail}>
      {items.slice(0, 5).map(renderItem)}
      {items.length > 5 && (
        <Collapsible open={expanded} onOpenChange={setExpanded}>
          <CollapsibleTrigger className={styles.more}>
            {expanded
              ? t("Show less")
              : t("Show {{count}} more", { count: items.length - 5 })}
          </CollapsibleTrigger>
          <CollapsiblePanel>
            <div className={styles.moreItems}>
              {items.slice(5).map(renderItem)}
            </div>
          </CollapsiblePanel>
        </Collapsible>
      )}
      {balance > 0 && (
        <div className={styles.item}>
          <span>
            {value.omitted_items
              ? t("Other entries ({{count}} omitted)", {
                  count: value.omitted_items,
                })
              : group === "skills"
                ? t("Catalog instructions")
                : t("Shared instructions")}
          </span>
          <span>{format(balance)}</span>
          <span className={styles.share}>{share(balance)}</span>
        </div>
      )}
    </div>
  );
}

export function PromptBreakdown({
  request,
  onGroupOpen,
}: {
  onGroupOpen?: () => void;
  request: Schema<"ContextUsageView">;
}) {
  const { t, i18n } = useTranslation();
  const breakdown = request.context_breakdown;
  if (!breakdown) return null;
  const total = breakdown.counted_input_characters;
  const groups = Object.entries(sections).flatMap(([key, [label, color]]) => {
    const value = breakdown.groups[key];
    return value ? [{ key, label, color, value }] : [];
  });
  if (!total || !groups.length) return null;
  const {
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    context_window: capacity,
  } = request;
  const missing = breakdown.unmeasured_content ?? [];
  const audio =
    missing.length === 1 && missing[0] === "audio"
      ? (request.input_audio_tokens ?? 0)
      : 0;
  const estimate =
    inputTokens != null &&
    inputTokens > 0 &&
    (missing.length === 0 || (audio > 0 && audio <= inputTokens));
  const textTokens = estimate ? inputTokens - audio : 0;
  const used = (inputTokens ?? 0) + (outputTokens ?? 0);
  const axis = Math.max(capacity ?? 0, used);
  const windowScale = !!(capacity && used > 0);
  const fraction = (chars: number) =>
    (chars / total) * (estimate ? textTokens / inputTokens : 1);
  const number = new Intl.NumberFormat(i18n.resolvedLanguage);
  const percentage = new Intl.NumberFormat(i18n.resolvedLanguage, {
    style: "percent",
    maximumFractionDigits: 1,
  });
  const format = (chars: number) =>
    number.format(estimate ? Math.round((chars / total) * textTokens) : chars);
  return (
    <section className={styles.section} aria-label={t("Prompt breakdown")}>
      {windowScale && (
        <div
          className={styles.track}
          role="meter"
          aria-label={t("Context used")}
          aria-valuenow={Math.min(used, capacity!)}
          aria-valuemax={capacity!}
          aria-valuemin={0}
          aria-valuetext={t("{{used, number}} of {{limit, number}} tokens", {
            used,
            limit: capacity!,
          })}
        >
          {estimate ? (
            groups.map(({ key, value, color }) => (
              <span
                key={key}
                style={{
                  width: `${(((value.characters / total) * textTokens) / axis) * 100}%`,
                  backgroundColor: color,
                }}
              />
            ))
          ) : (
            <span
              className={styles.input}
              style={{ width: `${((inputTokens ?? 0) / axis) * 100}%` }}
            />
          )}
          {audio > 0 && estimate && (
            <span
              className={styles.audio}
              style={{ width: `${(audio / axis) * 100}%` }}
            />
          )}
          {!!outputTokens && (
            <span
              className={styles.output}
              style={{ width: `${(outputTokens / axis) * 100}%` }}
            />
          )}
        </div>
      )}
      <div className={styles.heading}>
        <strong>{t("Prompt breakdown")}</strong>
        <span>{estimate ? t("Est. tokens") : t("Text characters")}</span>
        <span>{estimate ? t("Of input") : t("Of text")}</span>
      </div>
      {!windowScale && (
        <div className={styles.track} aria-label={t("Prompt breakdown")}>
          {groups.map(({ key, value, color }) => (
            <span
              key={key}
              style={{
                width: `${(value.characters / total) * 100}%`,
                backgroundColor: color,
              }}
            />
          ))}
        </div>
      )}
      {groups.map(({ key, label, color, value }) => {
        const items =
          Object.keys(value.items ?? {}).length + (value.omitted_items ?? 0);
        const row = (
          <>
            <i aria-hidden="true" style={{ backgroundColor: color }} />
            <span className={styles.name}>
              {t(label)}
              {key === "skills" && items > 0 && (
                <span className={styles.quantity}>{items}</span>
              )}
              {items > 0 && <CaretRight aria-hidden="true" />}
            </span>
            <span className={styles.amount}>{format(value.characters)}</span>
            <span className={styles.share}>
              {percentage.format(fraction(value.characters))}
            </span>
          </>
        );
        return items ? (
          <Collapsible
            key={key}
            onOpenChange={(open) => {
              if (open) onGroupOpen?.();
            }}
          >
            <CollapsibleTrigger className={styles.row}>
              {row}
            </CollapsibleTrigger>
            <CollapsiblePanel keepMounted>
              <GroupDetails
                color={color}
                group={key}
                value={value}
                format={format}
                share={(chars) => percentage.format(fraction(chars))}
              />
            </CollapsiblePanel>
          </Collapsible>
        ) : (
          <div key={key} className={styles.row}>
            {row}
          </div>
        );
      })}
      {estimate && audio > 0 && (
        <div className={styles.row}>
          <i className={styles.audio} />
          <span>{t("Audio")}</span>
          <span className={styles.amount}>{number.format(audio)}</span>
          <span className={styles.share}>
            {percentage.format(audio / inputTokens)}
          </span>
        </div>
      )}
      {outputTokens != null && outputTokens > 0 && (
        <div className={styles.outputRow}>
          <i className={styles.output} />
          <span>{t("Response output")}</span>
          <span>{number.format(outputTokens)}</span>
        </div>
      )}
      {!estimate && (
        <p className={styles.note}>
          {t("Token split unavailable. Shares cover measured text only.")}
        </p>
      )}
    </section>
  );
}
