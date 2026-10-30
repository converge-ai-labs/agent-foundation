import { BrandIcon, modelBrand } from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "./model-identity.module.css";

export function ModelIcon({
  model,
  name,
}: {
  model?: Schema<"ModelSummary">;
  name?: string;
}) {
  const route = model?.route ?? name ?? "";
  const [provider, ...parts] = route.split(":");
  const identity = modelBrand(
    parts.length ? parts.join(":") : route,
    provider?.startsWith("openai") ? "openai" : provider,
  );
  return <BrandIcon identity={identity} alias={identity} size={16} />;
}
export function ModelIdentity({
  model,
  name,
}: {
  model?: Schema<"ModelSummary">;
  name?: string;
}) {
  return (
    <span className={styles.identity}>
      <ModelIcon model={model} name={name} />
      <span>{name ?? model?.name ?? "Model"}</span>
    </span>
  );
}
