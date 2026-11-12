import { useTranslation } from "../i18n";
import { BrandIcon, modelBrand } from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "./model-identity.module.css";

export function ModelIcon({
  model,
  name,
  modelIdentity,
}: {
  model?: Schema<"ModelSummary">;
  name?: string;
  modelIdentity?: string;
}) {
  const route = modelIdentity ?? model?.route ?? name ?? "";
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
  modelIdentity,
}: {
  model?: Schema<"ModelSummary">;
  name?: string;
  modelIdentity?: string;
}) {
  const { t } = useTranslation();
  return (
    <span className={styles.identity}>
      <ModelIcon model={model} name={name} modelIdentity={modelIdentity} />
      <span>{name ?? model?.name ?? t("Model")}</span>
    </span>
  );
}
