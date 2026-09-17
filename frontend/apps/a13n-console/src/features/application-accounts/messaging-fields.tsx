import { ChoiceField, Label, Switch } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";

export const responseLabels = {
  mention: "Only when mentioned",
  discussion: "Continue an activated discussion",
  chat: "All supported human messages",
} as const;
export const placementLabels = {
  auto: "Let the agent decide",
  thread: "Thread or topic",
  main: "Main conversation",
} as const;
export const automaticPlacementHint =
  "The agent chooses where to reply. If unspecified, group replies go in a thread or topic; direct messages are answered in the chat.";

export function messagingPolicy(
  value: Record<string, unknown> | null | undefined,
): Schema["MessagingPolicy"] | null {
  const interaction = value?.interaction_mode,
    reply = value?.reply_mode;
  return (interaction === "mention" ||
    interaction === "discussion" ||
    interaction === "chat") &&
    (reply === "auto" || reply === "thread" || reply === "main")
    ? { interaction_mode: interaction, reply_mode: reply }
    : null;
}
export function MessagingFields({
  value,
  defaults,
  onChange,
}: {
  value: Record<string, unknown>;
  defaults: Record<string, unknown> | null | undefined;
  onChange: (value: Record<string, unknown>) => void;
}) {
  const { t } = useTranslation(),
    policy = messagingPolicy(value),
    inherited = messagingPolicy(defaults);
  const selected = policy ??
    inherited ?? { interaction_mode: "mention", reply_mode: "auto" };
  return (
    <>
      <Label>
        <Switch
          checked={!policy}
          onCheckedChange={(inherit) => onChange(inherit ? {} : selected)}
        />
        {t("Inherit bot response policy")}
      </Label>
      {policy ? (
        <>
          <ChoiceField
            label={t("When to respond")}
            value={policy.interaction_mode}
            onValueChange={(interaction_mode) =>
              onChange({ ...policy, interaction_mode })
            }
            options={Object.entries(responseLabels).map(([value, label]) => ({
              value,
              label: t(label),
            }))}
          />
          <ChoiceField
            label={t("Reply placement")}
            value={policy.reply_mode}
            onValueChange={(reply_mode) => onChange({ ...policy, reply_mode })}
            options={Object.entries(placementLabels).map(([value, label]) => ({
              value,
              label: t(label),
            }))}
          />
          <p>
            {t(
              "Discussion and whole-chat modes need the corresponding message subscriptions and history permissions on the provider.",
            )}
          </p>
          {policy.reply_mode !== "thread" && (
            <p>
              {t(
                policy.reply_mode === "main"
                  ? "Replies in the main conversation may be visible beyond the original discussion."
                  : automaticPlacementHint,
              )}
            </p>
          )}
        </>
      ) : (
        <p>
          {inherited
            ? `${t(responseLabels[inherited.interaction_mode])} · ${t(placementLabels[inherited.reply_mode])}`
            : t("The platform's default response policy applies.")}
        </p>
      )}
    </>
  );
}
