import {
  ChatCircleTextIcon,
  DatabaseIcon,
  GearSixIcon,
  LightningIcon,
  RobotIcon,
  StackIcon,
  WrenchIcon,
} from "@phosphor-icons/react";
import type { Schema } from "../../shared/api";
import styles from "./traces.module.css";

/** Open telemetry types retain a neutral fallback; names refine generic spans only. */
export function observationKind(
  observation: Pick<Schema["Observation"], "type" | "name">,
) {
  const type = observation.type.toLowerCase();
  if (["generation", "llm", "chat", "completion"].includes(type)) return "chat";
  if (type === "tool") return "tool";
  if (type === "agent") return "agent";
  if (type === "embedding" || type === "retriever") return "data";
  if (type === "event") return "event";
  if (type === "span") {
    if (["a13n.service.run_attempt", "harness.run"].includes(observation.name))
      return "agent";
    if (observation.name === "a13n.service.persist") return "data";
    if (/^(a13n\.service\.|harness\.)/.test(observation.name)) return "phase";
  }
  return "span";
}

const icons = {
  chat: ChatCircleTextIcon,
  tool: WrenchIcon,
  agent: RobotIcon,
  data: DatabaseIcon,
  event: LightningIcon,
  phase: GearSixIcon,
  span: StackIcon,
};

export function ObservationIcon({
  observation,
}: {
  observation: Schema["Observation"];
}) {
  const kind = observationKind(observation);
  const Icon = icons[kind];
  return (
    <span
      className={styles.observationIcon}
      data-kind={kind}
      aria-hidden="true"
    >
      <Icon size={17} />
      {(observation.status === "error" ||
        ["error", "fatal", "critical"].includes(observation.level ?? "")) && (
        <span className={styles.errorDot} />
      )}
    </span>
  );
}
