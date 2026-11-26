import { FormField, Textarea } from "a13n-ui";
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
  "zai",
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
    kind: string,
    detail?: string,
    preset?: Candidate["preset"],
  ) => (
    <button
      type="button"
      className={styles.engine}
      key={key}
      aria-pressed={engine === key}
      onClick={() => onEngine(key, preset)}
    >
      <span>{label}</span>
      <small>{detail || kind}</small>
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
                t("API Key"),
                item.credential_env &&
                  detectedEnvKeys.includes(item.credential_env)
                  ? t("Server key detected")
                  : undefined,
              ),
            )}
        </div>
        <ResourceChoice
          label={t("All API providers")}
          placeholder={t("Search connections…")}
          value={selected?.id ?? ""}
          options={native.map(({ id, label }) => ({ value: id, label }))}
          onValueChange={onEngine}
        />
        {editor}
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
    option(item.id, item.label, t("Native subscription"));
  const acpOption = (item: Preset) =>
    option(
      `acp:${item.id}`,
      ["codex", "github_copilot"].includes(item.id)
        ? `${item.name} CLI (ACP)`
        : item.name,
      t("Local CLI"),
      item.detected_path ? t("Local CLI · Installed") : undefined,
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
        <div className={styles.stack}>
          {option(
            featured.id,
            featured.label,
            t("Native subscription"),
            t("Recommended"),
          )}
          <p>
            {t(
              "Live Voice, Guardian Shell Review, Dictation, Sandbox, and usage tracking.",
            )}
          </p>
          {selected?.id === featured.id && editor}
        </div>
      )}
      <div className={styles.stack}>
        <h3>{t("Other subscriptions & local CLIs")}</h3>
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
              t("Native subscription"),
            )}
          {more.map(acpOption)}
          {option(
            "acp:custom",
            t("Custom ACP"),
            t("Local CLI"),
            undefined,
            "custom",
          )}
        </div>
      </details>
      {compareCodex && (
        <p>
          {t(
            "For Live Voice, Guardian Shell Review, Dictation, Sandbox, and usage tracking, choose the native Codex subscription.",
          )}
        </p>
      )}
      {selected && selected !== featured && editor}
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
  );
}
