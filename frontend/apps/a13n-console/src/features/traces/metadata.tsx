import { Badge } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { TraceJson } from "./content";
import { correlationAttributes } from "./correlation";
import styles from "./traces.module.css";

// Correlation has its own section, so chips keep to the remaining metadata.
const correlationKeys = new Set<string>(Object.values(correlationAttributes));

export function metadataChips(observation: Schema["Span"]) {
  const attributes = observation.attributes;
  const resource = observation.resource_attributes;
  const entries: { key: string; label: string; value: string }[] = [];
  const add = (key: string, label: string, value: unknown) => {
    if (["string", "number", "boolean"].includes(typeof value))
      entries.push({ key, label, value: String(value) });
  };
  add(
    "deployment.environment.name",
    "Environment",
    resource["deployment.environment.name"],
  );
  const labels = attributes["a13n.observation.labels"];
  if (Array.isArray(labels))
    labels.forEach((value, index) => add(`label:${index}`, "Label", value));
  for (const [key, value] of Object.entries(attributes)) {
    if (
      key.startsWith("a13n.observation.metadata.") &&
      !correlationKeys.has(key)
    )
      add(key, key.slice("a13n.observation.metadata.".length), value);
  }
  return entries;
}

export function MetadataChips({
  observation,
}: {
  observation: Schema["Span"];
}) {
  const { t } = useTranslation();
  const chips = metadataChips(observation);
  return (
    <div className={styles.chips}>
      {chips.slice(0, 4).map(({ key, label, value }) => (
        <Badge key={key} variant="secondary" title={`${key}: ${value}`}>
          <span>{t(label)}</span> {value}
        </Badge>
      ))}
      {chips.length > 4 && (
        <Badge variant="secondary" title={t("See all metadata in Attributes")}>
          +{chips.length - 4}
        </Badge>
      )}
    </div>
  );
}

export function AttributeValues({
  value,
}: {
  value: Schema["Span"]["attributes"];
}) {
  if (Object.keys(value).length === 0) return <TraceJson value={value} />;
  return (
    <dl className={styles.properties}>
      {Object.entries(value).map(([key, item]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>
            {typeof item === "object" ? (
              <TraceJson value={item} />
            ) : (
              String(item)
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
