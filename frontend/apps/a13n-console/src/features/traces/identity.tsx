import {
  CubeIcon,
  DatabaseIcon,
  GearSixIcon,
  LightningIcon,
  HeartIcon,
  StackIcon,
  WrenchIcon,
} from "@phosphor-icons/react";
import type { Schema } from "../../shared/api";
import styles from "./traces.module.css";

/** Open telemetry types retain a neutral fallback; names refine generic spans only. */
export function observationKind(
  observation: Pick<Schema["Span"], "kind" | "name">,
) {
  const type = observation.kind.toLowerCase();
  if (["generation", "llm", "chat", "completion"].includes(type)) return "chat";
  if (type === "tool") return "tool";
  if (type === "agent") return "agent";
  if (type === "embedding" || type === "retriever") return "data";
  if (type === "event") return "event";
  if (type === "span") {
    if (observation.name === "harness.run") return "agent";
    if (observation.name.startsWith("harness.")) return "phase";
  }
  return "span";
}

const icons = {
  chat: CubeIcon,
  tool: WrenchIcon,
  agent: HeartIcon,
  data: DatabaseIcon,
  event: LightningIcon,
  phase: GearSixIcon,
  span: StackIcon,
};

export function isFailed(observation: Schema["Span"]) {
  return (
    observation.status === "error" ||
    ["error", "fatal", "critical"].includes(observation.level ?? "")
  );
}

/** The kind mark alone, for identity cells that bring their own tile. */
export function ObservationGlyph({
  observation,
  size = 15,
}: {
  observation: Pick<Schema["Span"], "kind" | "name">;
  size?: number;
}) {
  const Icon = icons[observationKind(observation)];
  return <Icon size={size} aria-hidden="true" />;
}

/** The 22px kind tile that leads a timeline row. */
export function ObservationIcon({
  observation,
}: {
  observation: Schema["Span"];
}) {
  return (
    <span
      className={styles.observationIcon}
      data-kind={observationKind(observation)}
      aria-hidden="true"
    >
      <ObservationGlyph observation={observation} size={13} />
      {isFailed(observation) && <span className={styles.errorDot} />}
    </span>
  );
}
