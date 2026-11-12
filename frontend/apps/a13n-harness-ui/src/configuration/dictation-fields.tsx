import { useQuery } from "@tanstack/react-query";
import { Input } from "a13n-ui";
import { useTranslation } from "../i18n";
import { useSubscriptions } from "../setup/subscriptions";
import { ModelIcon } from "../shell/model-identity";
import { SettingsRow, SettingsSection } from "../shell/settings";
import { ErrorNotice } from "../shell/ui";
import { useSelectors, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { readDocument, updateDocument } from "./documents";
import { ResourceChoice } from "./resource-choice";
import layout from "./settings.module.css";

export function DictationFields({
  source,
  onChange,
}: {
  source: string;
  onChange: (source: string) => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const selectors = useSelectors();
  const doc = readDocument(source);
  const get = (path: string[]) => doc?.getIn(["dictation", ...path]);
  const codex = get(["backend"]) === "codex";
  const subscriptions = useSubscriptions(codex);
  const choices = useQuery({
    queryKey: ["model-choices"],
    enabled: codex,
    queryFn: ({ signal }) =>
      result(client.GET("/api/models/choices", { signal })),
  });
  function patch(values: [string[], unknown][]) {
    let next = source;
    for (const [path, value] of values)
      next = updateDocument(next, ["dictation", ...path], value);
    onChange(next);
  }
  const account = get(["codex", "account"]);
  const group = get(["codex", "group"]);
  return (
    <SettingsSection title={t("Dictation")} variant="grouped">
      <ResourceChoice
        label={t("Voice input")}
        description={t(
          "Turn speech into an editable draft. Nothing is sent automatically.",
        )}
        value={codex ? "codex" : String(get(["model"]) ?? "")}
        options={[
          { value: "", label: t("Off") },
          { value: "codex", label: t("Codex subscription") },
          ...(selectors.data?.models ?? [])
            .filter((m) => m.media_capabilities?.includes("audio"))
            .map((m) => ({
              value: m.model_id,
              label: m.name,
              icon: <ModelIcon model={m} />,
            })),
        ]}
        onValueChange={(value) =>
          patch(
            value === "codex"
              ? [[["backend"], "codex"]]
              : [
                  [["backend"], "model"],
                  [["model"], value || null],
                ],
          )
        }
      />
      {codex && (
        <>
          <ResourceChoice
            label={t("Subscription source")}
            description={t(
              "Uses the same Codex account for transcription and correction.",
            )}
            value={
              account ? `account:${account}` : group ? `group:${group}` : ""
            }
            loading={subscriptions.isPending}
            options={[
              { value: "", label: t("Default Codex group") },
              ...(subscriptions.data?.groups ?? [])
                .filter(
                  (g) => g.provider === "codex" && g.id !== "codex-default",
                )
                .map((g) => ({ value: `group:${g.id}`, label: g.name })),
              ...(subscriptions.data?.accounts ?? [])
                .filter((a) => a.account.provider === "codex")
                .map((a) => ({
                  value: `account:${a.account.id}`,
                  label: a.account.name,
                  disabled: !a.account.enabled || !a.authentication.usable,
                })),
            ]}
            onValueChange={(value) =>
              patch([
                [
                  ["codex", "account"],
                  value.startsWith("account:") ? value.slice(8) : undefined,
                ],
                [
                  ["codex", "group"],
                  value.startsWith("group:") ? value.slice(6) : undefined,
                ],
              ])
            }
          />
          {subscriptions.error && <ErrorNotice error={subscriptions.error} />}
        </>
      )}
      {(codex || !!get(["model"])) && (
        <details className={layout.groupDisclosure}>
          <summary>{t("Advanced dictation settings")}</summary>
          <div>
            {codex && (
              <>
                <SettingsRow
                  label={t("Speech recognition")}
                  description={t(
                    "Codex ASR chooses the speech model. No API key is needed.",
                  )}
                >
                  <span>{t("Codex ASR")}</span>
                </SettingsRow>
                <ResourceChoice
                  label={t("Correction model")}
                  description={t(
                    "A short, low-reasoning request uses your draft and nearby conversation to correct recognition errors.",
                  )}
                  value={String(get(["codex", "model"]) ?? "")}
                  options={[
                    { value: "", label: t("Preset Luna model") },
                    ...(
                      choices.data?.connections?.find((c) => c.id === "codex")
                        ?.models ?? []
                    ).map((m) => ({ value: m.value, label: m.label })),
                  ]}
                  loading={choices.isPending}
                  onValueChange={(value) =>
                    patch([[["codex", "model"], value || undefined]])
                  }
                />
                {choices.error && <ErrorNotice error={choices.error} />}
              </>
            )}
            <SettingsRow
              label={t("Context words on each side")}
              controlId="dictation-context-words"
              description={t(
                "Prioritize text around the cursor, then nearby conversation.",
              )}
            >
              <Input
                id="dictation-context-words"
                type="number"
                min={0}
                max={10000}
                value={Number(get(["context_words"]) ?? 2000)}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  if (Number.isInteger(n) && n >= 0 && n <= 10000)
                    patch([[["context_words"], n]]);
                }}
              />
            </SettingsRow>
          </div>
        </details>
      )}
    </SettingsSection>
  );
}
