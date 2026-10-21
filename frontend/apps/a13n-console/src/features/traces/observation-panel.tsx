import { DisclosureSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { Timestamp } from "../../shared/feedback";
import { CopyableId, CopyButton } from "../../shared/identity";
import { TraceContent, TraceJson } from "./content";
import { formatCost } from "../../shared/cost";
import { kindLabel, ObservationGlyph } from "./identity";
import { AttributeValues, MetadataChips } from "./metadata";
import {
  Duration,
  durationMs,
  Fact,
  LevelPill,
  TelemetryStatus,
} from "./values";
import styles from "./traces.module.css";

/** Panel header identity: kind mark, name, and the facts that fit one line. */
export function ObservationTitle({
  observation,
}: {
  observation: Schema["Span"];
}) {
  const { t } = useTranslation();
  const model = observation.model;
  const kind = kindLabel(t, observation.kind);
  const cost =
    observation.cost_usd === null ? null : formatCost(observation.cost_usd);
  return (
    <span className={styles.panelIdentity}>
      <span className={styles.panelGlyph} aria-hidden="true">
        <ObservationGlyph observation={observation} size={13} />
      </span>
      <span className={styles.panelIdentityCopy}>
        <strong title={observation.name}>{observation.name}</strong>
        <span className={styles.panelIdentityMeta}>
          <span title={kind}>{kind}</span>
          {model && <span title={model}>· {model}</span>}
          {durationMs(observation) !== null && (
            <span className={styles.panelIdentityNumber}>
              · <Duration observation={observation} />
            </span>
          )}
          {cost && <span className={styles.panelIdentityNumber}>· {cost}</span>}
        </span>
      </span>
    </span>
  );
}

/** Observation inspection: paired content first, then diagnostics and payloads. */
export function ObservationPanelBody({
  observation,
}: {
  observation: Schema["Span"];
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.panelStack}>
      <div className={styles.panelPair}>
        {(["input", "output"] as const).map((key) => (
          <section key={key} className={styles.panelSection}>
            <h3 className={styles.panelLabel}>
              {t(key === "input" ? "Input" : "Output")}
            </h3>
            <TraceContent content={observation[key]} />
          </section>
        ))}
      </div>
      <ObservationDiagnostics observation={observation} />
      <ObservationPayloads observation={observation} />
    </div>
  );
}

/**
 * Reported telemetry state, timing, identity and metadata of one observation.
 * Where the page summary already states the level, duration, cost, start and
 * metadata, `summarized` leaves them out.
 */
export function ObservationDiagnostics({
  observation,
  summarized = false,
}: {
  observation: Schema["Span"];
  summarized?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <section className={styles.panelSection}>
      <h3 className={styles.panelLabel}>{t("Diagnostics")}</h3>
      <dl className={styles.diagnosticValues}>
        <Fact label={t("Telemetry status")}>
          <TelemetryStatus observation={observation} />
        </Fact>
        {!summarized && (
          <>
            <Fact label={t("Level")}>
              <LevelPill level={observation.level} />
            </Fact>
            <Fact label={t("Duration")}>
              <Duration observation={observation} />
            </Fact>
            <Fact label={t("Cost")}>{formatCost(observation.cost_usd)}</Fact>
            <Fact label={t("Started")}>
              <Timestamp value={observation.started_at} />
            </Fact>
          </>
        )}
        <Fact label={t("Ended")}>
          <Timestamp value={observation.ended_at} />
        </Fact>
        {observation.model && (
          <Fact label={t("Model")}>{observation.model}</Fact>
        )}
      </dl>
      {observation.status_message !== null && (
        <pre className={styles.statusMessage}>{observation.status_message}</pre>
      )}
      <p className={styles.providerNote}>
        {t(
          "Telemetry status is the span's reported OpenTelemetry status, not the Run outcome. Unset and unavailable do not mean success.",
        )}
      </p>
      <div className={styles.identifier}>
        <span>
          {t("Observation ID")}
          <CopyableId value={observation.id} />
        </span>
        {observation.parent_id && (
          <span>
            {t("Parent ID")}
            <CopyableId value={observation.parent_id} />
          </span>
        )}
      </div>
      {!summarized && <MetadataChips observation={observation} />}
    </section>
  );
}

const payloads = [
  ["usage", "Usage"],
  ["attributes", "Attributes"],
  ["resource_attributes", "Resource attributes"],
  ["scope", "Scope"],
  ["events", "Events"],
  ["links", "Links"],
] as const;

/**
 * Retained telemetry payloads, each bounded to its own scroll. Attribute maps
 * read as a key list and carry their own raw-JSON copy; the remaining payloads
 * are code blocks, which already offer one.
 */
export function ObservationPayloads({
  observation,
}: {
  observation: Schema["Span"];
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.payloads}>
      {payloads.map(([key, label]) => {
        const structured =
          key === "attributes" || key === "resource_attributes";
        return (
          <DisclosureSection key={key} title={<>{t(label)}</>}>
            <div className={styles.payloadBlock}>
              {structured && (
                <div className={styles.payloadToolbar}>
                  <CopyButton
                    value={() =>
                      JSON.stringify(observation[key], null, 2) ?? "null"
                    }
                    iconOnly
                    copyLabel={t("Copy raw JSON")}
                  />
                </div>
              )}
              <div
                className={`${styles.payloadBody} a13n-scrollbar`}
                data-structured={structured || undefined}
              >
                {structured ? (
                  <AttributeValues value={observation[key]} />
                ) : (
                  <TraceJson value={observation[key]} />
                )}
              </div>
            </div>
          </DisclosureSection>
        );
      })}
    </div>
  );
}
