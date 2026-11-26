import { useTranslation } from "../i18n";
import { BrandIcon, modelBrand } from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "./model-identity.module.css";

const acpPresetBrands: Record<string, string> = {
  antigravity: "antigravity",
  claude_code: "claudecode",
  codex: "codex",
  gemini_cli: "geminicli",
  github_copilot: "githubcopilot",
  opencode: "opencode",
  goose: "goose",
  kimi_cli: "kimi",
};

export function acpPresetBrand(preset?: string | null) {
  return preset ? acpPresetBrands[preset] : undefined;
}

export function acpModelIdentity(
  name?: string,
  value?: string,
  preset?: string | null,
) {
  return (
    (name ? modelBrand(name) : undefined) ??
    (value ? modelBrand(value) : undefined) ??
    acpPresetBrand(preset)
  );
}

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
