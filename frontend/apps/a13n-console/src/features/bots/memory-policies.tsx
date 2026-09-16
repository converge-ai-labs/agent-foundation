import { Button, Checkbox, FormField, Input, Label, ModalFrame } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { Pagination, useCursor } from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { refreshMemory, unconfirmedWrite } from "./memory-actions";
import { GroupPicker, useMemoryGroups } from "./memory-groups";
import styles from "./bots.module.css";
import shared from "../../shared/shared.module.css";

type Policy = Schema["SharingPolicy"];
export function MemoryPolicies({ account }: { account: Schema["Account"] }) {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={(value) => {
        if (!busy) setOpen(value);
      }}
      title={t("Sharing settings")}
      size="lg"
      closeLabel={t("Close")}
      trigger={
        <Button type="button" size="sm" variant="outline">
          {t("Sharing settings")}
        </Button>
      }
    >
      {open && <Policies account={account} setBusy={setBusy} />}
    </ModalFrame>
  );
}
function Policies({
  account,
  setBusy,
}: {
  account: Schema["Account"];
  setBusy: (busy: boolean) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    page = useCursor();
  const [selected, setSelected] = useState<Policy | null>();
  const query = useQuery({
    queryKey: [
      "bot-memory-policies",
      account.id,
      account.memory?.provider_id,
      page.cursor,
    ],
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/application-accounts/{account_id}/memory-sharing-policies",
          {
            params: {
              path: { account_id: account.id },
              query: { cursor: page.cursor },
            },
            signal,
          },
        )
        .then(data),
  });
  if (selected !== undefined)
    return (
      <PolicyForm
        account={account}
        policy={selected}
        setBusy={setBusy}
        close={() => setSelected(undefined)}
      />
    );
  return (
    <div className={styles.publications}>
      <p>
        {t(
          "Groups are independent by default. A sharing policy lets its participants read each other's qualifying memory. Explicitly published copies remain separate.",
        )}
      </p>
      <Button type="button" variant="outline" onClick={() => setSelected(null)}>
        {t("New sharing policy")}
      </Button>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : (
        !query.error && (
          <>
            {query.data?.items.map((policy) => (
              <button
                type="button"
                key={policy.id}
                className={styles.documentItem}
                onClick={() => setSelected(policy)}
              >
                <span>
                  <strong>{policy.name}</strong>
                  <small>
                    {t("Participants")}: {policy.scope_ids.length}
                  </small>
                </span>
                <StateBadge state={policy.enabled ? "enabled" : "disabled"} />
              </button>
            ))}
            {!query.data?.items.length && (
              <p>{t("No continuing sharing policies.")}</p>
            )}
          </>
        )
      )}
      <Pagination page={page} next={query.data?.next_cursor} />
    </div>
  );
}
function PolicyForm({
  account,
  policy,
  setBusy,
  close,
}: {
  account: Schema["Account"];
  policy: Policy | null;
  setBusy: (busy: boolean) => void;
  close: () => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    groups = useMemoryGroups(account);
  const [name, setName] = useState(policy?.name ?? ""),
    [ids, setIds] = useState<string[]>(policy?.scope_ids ?? []);
  const [kinds, setKinds] = useState<("daily" | "long_term")[]>(
    policy?.kinds ?? ["long_term"],
  );
  const [history, setHistory] = useState(policy?.include_history ?? false),
    [future, setFuture] = useState(policy?.enroll_future_groups ?? false),
    [enabled, setEnabled] = useState(policy?.enabled ?? true);
  const [review, setReview] = useState(false),
    [uncertain, setUncertain] = useState(false);
  const save = useMutation({
    retry: false,
    onMutate: () => setBusy(true),
    onSettled: () => setBusy(false),
    mutationFn: async () => {
      const body = {
        name,
        scope_ids: ids,
        kinds,
        include_history: history,
        enroll_future_groups: future,
        enabled,
      };
      return policy
        ? client.http
            .PUT(
              "/api/v1/application-accounts/{account_id}/memory-sharing-policies/{policy_id}",
              {
                params: {
                  path: { account_id: account.id, policy_id: policy.id },
                },
                body: { ...body, expected_version: policy.version },
              },
            )
            .then(data)
        : client.http
            .POST(
              "/api/v1/application-accounts/{account_id}/memory-sharing-policies",
              { params: { path: { account_id: account.id } }, body },
            )
            .then(data);
    },
    onSuccess: async () => {
      await refreshMemory(cache, account.id);
      close();
    },
    onError: (error) => {
      if (unconfirmedWrite(error)) setUncertain(true);
    },
  });
  const resetBoundary =
    !policy ||
    (!policy.enabled && enabled) ||
    (policy.include_history && !history);
  const groupName = (id: string) =>
    groups.data?.find((group) => group.id === id)?.name ?? id;
  return (
    <form
      className={shared.stack}
      onSubmit={(event) => {
        event.preventDefault();
        if (review) save.mutate();
        else setReview(true);
      }}
    >
      {!review ? (
        <fieldset
          disabled={save.isPending || uncertain}
          className={styles.policyFields}
        >
          <FormField label={t("Policy name")}>
            <Input
              value={name}
              required
              maxLength={128}
              onChange={(event) => setName(event.target.value)}
            />
          </FormField>
          <Label className={styles.groupOption}>
            <Checkbox
              checked={enabled}
              onCheckedChange={(value) => setEnabled(!!value)}
            />
            {t("Enable mutual sharing")}
          </Label>
          <GroupPicker
            groups={groups}
            label="Participants"
            description="Each participant can read qualifying memory from every other participant."
            value={ids}
            onChange={setIds}
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() =>
              setIds(
                (groups.data ?? [])
                  .filter(
                    (group) =>
                      group.enabled &&
                      ["public", "private"].includes(group.audience),
                  )
                  .map((group) => group.id),
              )
            }
          >
            {t("Select all currently eligible groups")}
          </Button>
          <fieldset className={styles.groupPicker}>
            <legend>{t("Content kinds")}</legend>
            <div className={styles.policyKinds}>
              {(["long_term", "daily"] as const).map((kind) => (
                <Label key={kind} className={styles.groupOption}>
                  <Checkbox
                    checked={kinds.includes(kind)}
                    onCheckedChange={(checked) =>
                      setKinds(
                        checked
                          ? [...kinds, kind]
                          : kinds.filter((value) => value !== kind),
                      )
                    }
                  />
                  {t(kind === "daily" ? "Daily" : "Long-term")}
                </Label>
              ))}
            </div>
          </fieldset>
          <Label className={styles.groupOption}>
            <Checkbox
              checked={history}
              onCheckedChange={(value) => setHistory(!!value)}
            />
            {t("Include historical memory")}
          </Label>
          <Label className={styles.groupOption}>
            <Checkbox
              checked={future}
              onCheckedChange={(value) => setFuture(!!value)}
            />
            {t("Automatically include newly configured groups")}
          </Label>
          <p>
            {t(
              "Selecting all current groups does not include history or future groups. Direct conversations cannot participate.",
            )}
          </p>
        </fieldset>
      ) : (
        <div className={styles.draftPreview}>
          <h3>{name}</h3>
          <p>
            {t(
              enabled
                ? "Mutual sharing will be enabled."
                : "This policy will stop granting access. Local memory and other sharing grants remain unchanged.",
            )}
          </p>
          <h4>{t("Participants")}</h4>
          <p>{ids.map(groupName).join(", ")}</p>
          <p>
            {t("Content kinds")}:{" "}
            {kinds
              .map((kind) => t(kind === "daily" ? "Daily" : "Long-term"))
              .join(", ")}
          </p>
          <p>
            {t(
              history
                ? "Includes historical and newly saved memory."
                : "Only newly saved memory is shared; activity dates do not change eligibility.",
            )}
          </p>
          {!history && enabled && (
            <p>
              {resetBoundary ? (
                t(
                  "A new save cutoff will be set when you confirm. Earlier documents will not become shared.",
                )
              ) : (
                <>
                  {t("Policy save cutoff")}:{" "}
                  <Timestamp value={policy.future_since} />
                </>
              )}
            </p>
          )}
          <p>
            {t(
              future
                ? "New groups join when they first save a verified, enabled memory configuration. Previously configured or manually removed groups are not automatically added."
                : "New groups remain independent until explicitly added.",
            )}
          </p>
          {!history && enabled && (
            <p>
              {t(
                "Each group's join time also applies. Later participants can only read and contribute records saved after they join.",
              )}
            </p>
          )}
          <p>
            {t(
              "Removing a participant ends access through this policy, but another policy or published copy may still grant access.",
            )}
          </p>
        </div>
      )}
      {policy && (
        <details>
          <summary>{t("Saved participation dates")}</summary>
          <ul>
            {policy.participants.map((item) => (
              <li key={item.scope_id}>
                {groupName(item.scope_id)} ·{" "}
                <Timestamp value={item.joined_at} />
              </li>
            ))}
          </ul>
        </details>
      )}
      <ErrorNotice error={save.error} />
      {uncertain && (
        <p role="alert">
          {t(
            "The save result is unknown. Return to the policy list and inspect it before submitting another change.",
          )}
        </p>
      )}
      <div className={styles.memoryActions}>
        <Button
          type="button"
          variant="outline"
          disabled={save.isPending}
          onClick={close}
        >
          {t("Back to policies")}
        </Button>
        {review && (
          <Button
            type="button"
            variant="outline"
            disabled={save.isPending || uncertain}
            onClick={() => setReview(false)}
          >
            {t("Back to draft")}
          </Button>
        )}
        <Button
          type="submit"
          disabled={
            save.isPending ||
            uncertain ||
            !name.trim() ||
            ids.length < 2 ||
            ids.length > 128 ||
            !kinds.length
          }
        >
          {t(review ? "Confirm sharing policy" : "Review confirmation")}
        </Button>
      </div>
    </form>
  );
}
