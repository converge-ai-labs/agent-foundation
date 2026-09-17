import { ChoiceField, FormField, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";

export function GitHubPolicyFields({
  value,
  onChange,
  polling = false,
}: {
  value: Schema["GitHubReceptionPolicy"];
  onChange: (value: Schema["GitHubReceptionPolicy"]) => void;
  polling?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <>
      <FormField
        label={t("Allowed GitHub senders")}
        description={t(
          "Comma-separated GitHub usernames, or * for everyone. Unattributable notifications are ignored when usernames are specified.",
        )}
      >
        <Input
          value={(value.allowed_senders ?? ["*"]).join(", ")}
          onChange={(event) =>
            onChange({
              ...value,
              allowed_senders: event.target.value
                .split(",")
                .map((name) => name.trim()),
            })
          }
          required
        />
      </FormField>
      {polling ? (
        <p>
          {t(
            "Notifications wake the agent to inspect the current Issue or PR. Updates may be combined; a mention reason does not mean every update mentions the bot.",
          )}
        </p>
      ) : (
        <ChoiceField
          label={t("GitHub events")}
          value={value.event_actions?.length ? "comments" : "all"}
          onValueChange={(choice) =>
            onChange({
              ...value,
              event_actions:
                choice === "comments"
                  ? [
                      "issue_comment.created",
                      "pull_request_review_comment.created",
                    ]
                  : [],
            })
          }
          options={[
            { value: "all", label: t("All supported events") },
            { value: "comments", label: t("New comments only") },
          ]}
        />
      )}
      <p>
        {t(
          "The agent replies with an ordinary Issue or PR comment. Repository permissions and the selected agent's tools determine available actions.",
        )}
      </p>
    </>
  );
}
