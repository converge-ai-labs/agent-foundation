import { FormField, Textarea } from "a13n-ui";
import { Check, Info, Terminal } from "@phosphor-icons/react";
import { acpPresetBrand, ModelIcon } from "../shell/model-identity";
import { AcpAuth } from "../configuration/acp-auth";
import {
  ModelEditor,
  type ModelEditorDraft,
} from "../configuration/model-editor";
import { ResourceChoice } from "../configuration/resource-choice";
import { useTranslation } from "../i18n";
import { TextField } from "../shell/ui";
import type { Schema } from "../transport/client";
import styles from "./wizard.module.css";

export type SetupTrack = "subscription" | "api";
type Candidate = Schema<"AcpAgentCandidate">;
type Preset = Schema<"AcpPresetView">;
type Recipe = Schema<"ModelRecipe">;
type Connection = Schema<"ModelConnection">;
const COMMON_API = [
  "openai-responses",
  "anthropic",
  "google",
  "openrouter",
  "deepseek",
  "moonshotai",
];
const MAJOR_ACP = ["antigravity", "claude_code", "gemini_cli"];
const NATIVE_ACP: Record<string, string> = {
  codex_subscription: "codex",
  copilot_subscription: "github_copilot",
};

export function importablePresets(
  presets: readonly Preset[],
  status: Schema<"SetupStatus">,
  candidate?: Candidate | null,
  authentication?: string,
) {
  const excluded = new Set([
    ...Object.values(status.acp_presets ?? {}),
    candidate?.preset,
    ...(authentication ? [NATIVE_ACP[authentication]] : []),
  ]);
  return presets.filter(
    (preset) => preset.detected_path && !excluded.has(preset.id),
  );
}

export function SetupConnections({
  track,
  engine,
  connections,
  presets,
  detectedEnvKeys,
  draft,
  recipe,
  candidate,
  onEngine,
  onCandidate,
  onDraft,
  onModel,
  onReady,
}: {
  track: SetupTrack;
  engine: string;
  connections: readonly Connection[];
  presets: readonly Preset[];
  detectedEnvKeys: readonly string[];
  draft?: ModelEditorDraft;
  recipe: Recipe | null;
  candidate?: Candidate | null;
  onEngine: (value: string, preset?: Candidate["preset"]) => void;
  onCandidate: (value: Candidate) => void;
  onDraft: (draft: ModelEditorDraft) => void;
  onModel: (recipe: Recipe) => void;
  onReady: (ready: boolean, needsSignIn?: boolean) => void;
}) {
  const { t } = useTranslation();
  const native = connections.filter((item) =>
    track === "api" ? item.authentication === "api_key" : !!item.account,
  );
  const selected = native.find(({ id }) => id === engine);
  const external = presets.find(({ id }) => `acp:${id}` === engine);
  const custom = engine === "acp:custom";
  const editor = selected && (
    <ModelEditor
      key={`editor:${selected.id}`}
      draft={
        draft?.connection === selected.id
          ? draft
          : {
              connection: selected.id,
              modelId: selected.default_model,
              baseUrl: selected.base_url ?? "",
            }
      }
      onDraftChange={onDraft}
      value={recipe}
      onChange={onModel}
      onReady={onReady}
      setup={{ detectedEnvKeys }}
    />
  );
  const option = (
    key: string,
    label: string,
    identity: string,
    detail: string,
    preset?: Candidate["preset"],
  ) => (
    <button
      type="button"
      className={styles.engine}
      key={key}
      aria-pressed={engine === key}
      onClick={() => onEngine(key, preset)}
    >
      {preset === "custom" ? (
        <Terminal size={16} aria-hidden />
      ) : (
        <ModelIcon modelIdentity={identity} />
      )}
      <span className={styles.engineText}>
        <span>{label}</span>
        <small>{detail}</small>
      </span>
      <Check
        size={14}
        weight="bold"
        className={styles.selectedMark}
        aria-hidden
      />
    </button>
  );
  if (track === "api") {
    return (
      <div className={styles.stack}>
        <div
          className={styles.engineGrid}
          role="group"
          aria-label={t("Common API providers")}
        >
          {COMMON_API.map((id) => native.find((item) => item.id === id))
            .filter((item) => item !== undefined)
            .map((item) =>
              option(
                item.id,
                item.label,
                item.provider,
                item.credential_env &&
                  detectedEnvKeys.includes(item.credential_env)
                  ? t("Server key detected")
                  : t("API Key"),
              ),
            )}
        </div>
        <ResourceChoice
          label={t("All API providers")}
          placeholder={t("Search connections…")}
          value={selected?.id ?? ""}
          options={native.map(({ id, label, provider }) => ({
            value: id,
            label,
            icon: <ModelIcon modelIdentity={provider} />,
          }))}
          onValueChange={onEngine}
        />
        {editor && <div className={styles.editor}>{editor}</div>}
      </div>
    );
  }
  const featured = native.find(
    ({ authentication }) => authentication === "codex_subscription",
  );
  const chatgpt = native.find(
    ({ authentication }) => authentication === "chatgpt_subscription",
  );
  const major = presets
    .filter(({ id }) => MAJOR_ACP.includes(id))
    .sort((a, b) => Number(!!b.detected_path) - Number(!!a.detected_path));
  const more = presets.filter(
    ({ id }) => !MAJOR_ACP.includes(id) && id !== "custom",
  );
  const nativeOption = (item: Connection) =>
    option(
      item.id,
      item.label,
      item.account?.provider ?? item.provider,
      t("Native subscription"),
    );
  const acpOption = (item: Preset) =>
    option(
      `acp:${item.id}`,
      ["codex", "github_copilot"].includes(item.id)
        ? `${item.name} CLI (ACP)`
        : item.name,
      acpPresetBrand(item.id) ?? item.id,
      item.detected_path ? t("Local CLI · Installed") : t("Local CLI"),
      item.id,
    );
  const compareCodex =
    selected?.authentication === "chatgpt_subscription" ||
    external?.id === "codex";
  const selectedCandidate =
    candidate &&
    (!custom ||
      (candidate.command?.trim() &&
        !candidate.command.includes("\0") &&
        !candidate.args?.some((arg) => arg.includes("\0"))))
      ? candidate
      : undefined;
  return (
    <div className={styles.stack}>
      {featured && (
        <button
          type="button"
          className={`${styles.choiceCard} ${styles.featured}`}
          aria-pressed={engine === featured.id}
          onClick={() => onEngine(featured.id)}
        >
          <span className={styles.cardTitle}>
            <ModelIcon modelIdentity="codex" />
            <strong>{featured.label}</strong>
            <small className={styles.badge}>{t("Recommended")}</small>
            <Check
              size={14}
              weight="bold"
              className={styles.selectedMark}
              aria-hidden
            />
          </span>
          <span className={styles.cardDescription}>
            {t(
              "Live Voice, Guardian Shell Review, Dictation, Sandbox, and usage tracking.",
            )}
          </span>
        </button>
      )}
      <div className={styles.sectionGroup}>
        <h3 className={styles.sectionHeading}>
          {t("Other subscriptions & local CLIs")}
        </h3>
        <div className={styles.engineGrid}>
          {native
            .filter((item) => item !== featured && item !== chatgpt)
            .map(nativeOption)}
          {major.map(acpOption)}
        </div>
      </div>
      <details
        className={styles.advanced}
        open={
          engine === chatgpt?.id ||
          custom ||
          more.some(({ id }) => engine === `acp:${id}`) ||
          undefined
        }
      >
        <summary>{t("More options")}</summary>
        <div className={styles.engineGrid}>
          {chatgpt &&
            option(
              chatgpt.id,
              t("Sign in with ChatGPT"),
              "openai",
              t("Native subscription"),
            )}
          {more.map(acpOption)}
          {option("acp:custom", t("Custom ACP"), "", t("Local CLI"), "custom")}
        </div>
      </details>
      {compareCodex && (
        <p className={styles.comparison}>
          <Info size={16} aria-hidden />
          <span>
            {t(
              "For Live Voice, Guardian Shell Review, Dictation, Sandbox, and usage tracking, choose the native Codex subscription.",
            )}
          </span>
        </p>
      )}
      {(editor || (custom && candidate) || (external && selectedCandidate)) && (
        <div className={`${styles.editor} ${styles.stack}`}>
          {editor}
          {custom && candidate && (
            <>
              <TextField
                label={t("Executable")}
                value={candidate.command ?? ""}
                onChange={(command) => {
                  onReady(false);
                  onCandidate({ ...candidate, command });
                }}
              />
              <FormField label={t("Arguments (one per line)")}>
                <Textarea
                  rows={3}
                  value={candidate.args?.join("\n") ?? ""}
                  onChange={(event) => {
                    onReady(false);
                    const args = event.target.value;
                    onCandidate({
                      ...candidate,
                      args: args ? args.split("\n") : [],
                    });
                  }}
                />
              </FormField>
              <p>
                {t(
                  "Keep credentials in the CLI's account store or environment, not in command arguments.",
                )}
              </p>
            </>
          )}
          {(external || custom) && selectedCandidate && (
            <AcpAuth
              key={JSON.stringify(selectedCandidate)}
              candidate={selectedCandidate}
              detected={!!external?.detected_path}
              installUrl={external?.install_url}
              onReady={onReady}
            />
          )}
          {custom && !selectedCandidate && candidate?.command && (
            <p role="alert">{t("Enter a valid executable and arguments.")}</p>
          )}
        </div>
      )}
    </div>
  );
}
