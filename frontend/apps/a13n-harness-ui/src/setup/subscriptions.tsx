import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BrandIcon,
  Button,
  ChoiceField,
  FormField,
  Input,
  Menu,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
  ModalFrame,
} from "a13n-ui";
import {
  ArrowsClockwiseIcon,
  DotsThreeIcon,
  KeyIcon,
  PauseIcon,
  PencilSimpleIcon,
  PlayIcon,
  TicketIcon,
  TrashIcon,
  WalletIcon,
} from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import styles from "./subscriptions.module.css";
import { GrokAllowance } from "./provider-usage";
import {
  availableVouchers,
  compactAllowance,
  quotaTime,
  voucherSummary,
  windowLabel,
} from "./subscription-usage";

type Account = Schema<"SubscriptionAccount">;
type AccountView = Schema<"SubscriptionAccountView">;
type Group = Schema<"SubscriptionGroupResource">;

export function useSubscriptions(enabled = true) {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["subscriptions"],
    enabled,
    queryFn: ({ signal }) =>
      result(client.GET("/api/subscriptions", { signal })),
    // This reads the shared cache; provider polling belongs to the Host.
    refetchInterval: 15_000,
  });
}

function WindowUsage({
  window,
  label,
}: {
  window: Pick<Schema<"UsageWindow">, "used_percent" | "reset_at">;
  label: string;
}) {
  const remaining = Math.max(0, Math.min(100, 100 - window.used_percent));
  return (
    <div className={styles.meter}>
      <div className={styles.meterHeading}>
        <span>{label}</span>
        <span>
          <strong>{Math.round(remaining)}%</strong> left
        </span>
      </div>
      <meter
        min={0}
        max={100}
        value={remaining}
        aria-label={`${label} remaining`}
      />
      <span
        className={styles.quotaDetail}
        title={
          window.reset_at > 0 ? quotaTime(window.reset_at * 1000) : undefined
        }
      >
        {window.reset_at > 0
          ? `Resets ${quotaTime(window.reset_at * 1000, true)}`
          : "Reset time unavailable"}
      </span>
    </div>
  );
}

function LimitUsage({
  limits,
  label,
}: {
  limits: Schema<"UsageLimits"> | null | undefined;
  label: string;
}) {
  const windows = [limits?.primary_window, limits?.secondary_window].filter(
    (window) => window != null,
  );
  return (
    <div className={styles.quotaGroup}>
      {label && <span className={styles.quotaLabel}>{label}</span>}
      <div className={styles.quotaWindows}>
        {windows.map((window, index) => (
          <WindowUsage
            key={index}
            window={window}
            label={windowLabel(window.limit_window_seconds)}
          />
        ))}
        {!windows.length && <span>Usage windows unavailable</span>}
      </div>
      {limits && (!limits.allowed || limits.limit_reached) && (
        <span>Included allowance unavailable</span>
      )}
    </div>
  );
}

function VoucherSummary({
  usage,
}: {
  usage: Schema<"CodexUsage"> | null | undefined;
}) {
  const vouchers = voucherSummary(usage);
  return (
    <div className={styles.voucherSummary}>
      <span className={styles.summaryItem}>
        <TicketIcon size={15} aria-hidden />
        Reset vouchers ·{" "}
        {vouchers.count === null
          ? "Unavailable"
          : `${vouchers.count} available`}
      </span>
      {vouchers.expiresAt !== null && (
        <span className={styles.quotaDetail}>
          Expires {quotaTime(vouchers.expiresAt)}
        </span>
      )}
      {vouchers.count !== null &&
        vouchers.count > 0 &&
        vouchers.expiresAt === null && <span>Expiry not provided</span>}
      {usage?.reset_unavailable && (
        <span>Voucher details could not be refreshed</span>
      )}
    </div>
  );
}

export function AccountUsage({ view }: { view: AccountView }) {
  const observed = view.usage.usage;
  const usage = observed && "account_id" in observed ? observed : null;
  if (view.account.provider === "grok" || observed?.provider === "grok") {
    return (
      <div className={styles.usage}>
        <GrokAllowance
          usage={observed?.provider === "grok" ? observed : null}
        />
        <UsageFreshness snapshot={view.usage} />
      </div>
    );
  }
  const monthly = usage?.spend_control?.individual_limit;
  return (
    <div className={styles.usage}>
      <LimitUsage limits={usage?.rate_limit} label="" />
      {(usage?.additional_rate_limits ?? []).map((limit, index) => (
        <LimitUsage
          key={`${limit.metered_feature}-${index}`}
          limits={limit.rate_limit}
          label={
            limit.normal_model_slug
              ? `${limit.limit_name} · ${limit.normal_model_slug}`
              : limit.limit_name
          }
        />
      ))}
      {monthly && (
        <div className={styles.quotaGroup}>
          <WindowUsage window={monthly} label="Monthly credit limit" />
          <span>
            {monthly.used} / {monthly.limit} credits used · {monthly.remaining}{" "}
            remaining
          </span>
        </div>
      )}
      {usage?.spend_control?.reached && (
        <span>Workspace spending limit reached</span>
      )}
      <div className={styles.balanceSummary}>
        <VoucherSummary usage={usage} />
        {usage?.credits && (
          <span className={styles.summaryItem}>
            <WalletIcon size={15} aria-hidden />
            Credits ·{" "}
            {usage.credits.unlimited
              ? "Unlimited"
              : (usage.credits.balance ??
                (usage.credits.has_credits ? "Available" : "None"))}
          </span>
        )}
      </div>
      <UsageFreshness snapshot={view.usage} />
    </div>
  );
}

function UsageFreshness({ snapshot }: { snapshot: Schema<"UsageSnapshot"> }) {
  return (
    <span className={styles.quotaDetail}>
      {snapshot.error ? "Refresh failed · " : ""}
      {snapshot.observed_at
        ? `Updated ${quotaTime(snapshot.observed_at * 1000)}`
        : "Not refreshed yet"}
    </span>
  );
}

export function SubscriptionAccounts({
  renderLogin,
}: {
  renderLogin: (account: Account) => ReactNode;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const catalog = useSubscriptions();
  const [creating, setCreating] = useState(false);
  const [provider, setProvider] = useState<"codex" | "grok">("codex");
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<Account | null>(null);
  const [login, setLogin] = useState<Account | null>(null);
  const [deleting, setDeleting] = useState<Account | null>(null);
  const [reset, setReset] = useState<Account | null>(null);
  const [group, setGroup] = useState<Group | "new" | null>(null);
  const [filter, setFilter] = useState("");
  const invalidate = () =>
    queries.invalidateQueries({ queryKey: ["subscriptions"] });
  const create = useMutation({
    mutationFn: () =>
      result(
        client.POST("/api/subscriptions/accounts", {
          body: { provider, name: name.trim() },
        }),
      ),
    onSuccess: (account) => {
      setCreating(false);
      setLogin(account);
      setName("");
      void invalidate();
    },
  });
  const update = useMutation({
    mutationFn: ({
      id,
      ...body
    }: {
      id: string;
      name?: string;
      enabled?: boolean;
    }) =>
      result(
        client.PATCH("/api/subscriptions/accounts/{account_id}", {
          params: { path: { account_id: id } },
          body,
        }),
      ),
    onSuccess: () => {
      setEditing(null);
      void invalidate();
    },
  });
  const remove = useMutation({
    mutationFn: (account: Account) =>
      result(
        client.DELETE("/api/subscriptions/accounts/{account_id}", {
          params: { path: { account_id: account.id } },
        }),
      ),
    onSuccess: () => {
      setDeleting(null);
      void invalidate();
    },
  });
  const refresh = useMutation({
    mutationFn: (id: string) =>
      result(
        client.POST("/api/subscriptions/accounts/{account_id}/refresh", {
          params: { path: { account_id: id } },
        }),
      ),
    onSuccess: () => {
      void invalidate();
    },
  });
  const accounts = catalog.data?.accounts ?? [];
  return (
    <section className={styles.section} aria-label="Subscription accounts">
      <header className={styles.heading}>
        <div>
          <h2>Subscription accounts</h2>
          <p>Allowance refreshes automatically, or on demand.</p>
        </div>
        <Button
          onClick={() => {
            setName("");
            create.reset();
            setCreating(true);
          }}
        >
          Add account
        </Button>
      </header>
      <ErrorNotice error={catalog.error || update.error || refresh.error} />
      <Input
        aria-label="Filter subscription accounts"
        placeholder="Find an account…"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        className={styles.search}
      />
      {catalog.isPending && <p role="status">Loading accounts…</p>}
      <div className={styles.rows}>
        {accounts
          .filter(({ account }) =>
            `${account.name} ${account.provider}`
              .toLowerCase()
              .includes(filter.toLowerCase()),
          )
          .map((view) => {
            const { account, authentication } = view;
            return (
              <article key={account.id} className={styles.accountCard}>
                <header className={styles.accountHeader}>
                  <div className={styles.accountIdentity}>
                    <BrandIcon
                      identity={account.provider === "codex" ? "openai" : "xai"}
                      size={24}
                    />
                    <div className={styles.identity}>
                      <div className={styles.accountName}>
                        <strong>{account.name}</strong>
                        {view.usage.usage &&
                          "plan_type" in view.usage.usage &&
                          view.usage.usage.plan_type !== "unknown" && (
                            <span className={styles.plan}>
                              {view.usage.usage.plan_type}
                            </span>
                          )}
                      </div>
                      <span className={styles.accountMeta}>
                        {account.provider === "codex" ? "Codex" : "Grok"}
                        <span
                          className={styles.status}
                          data-connected={
                            account.enabled &&
                            authentication.availability === "available"
                          }
                        >
                          {account.enabled
                            ? authentication.availability === "available"
                              ? "Connected"
                              : "Sign-in required"
                            : "Paused"}
                        </span>
                      </span>
                    </div>
                  </div>
                  <div className={styles.cardActions}>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Refresh"
                      loading={
                        refresh.isPending && refresh.variables === account.id
                      }
                      onClick={() => refresh.mutate(account.id)}
                    >
                      <ArrowsClockwiseIcon />
                    </Button>
                    <Menu>
                      <MenuTrigger
                        render={
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label="Manage account"
                          />
                        }
                      >
                        <DotsThreeIcon />
                      </MenuTrigger>
                      <MenuPopup align="end">
                        <MenuItem onClick={() => setLogin(account)}>
                          <KeyIcon />
                          Sign-in & logout
                        </MenuItem>
                        <MenuItem
                          onClick={() => {
                            setName(account.name);
                            setEditing(account);
                          }}
                        >
                          <PencilSimpleIcon />
                          Rename
                        </MenuItem>
                        <MenuItem
                          disabled={update.isPending}
                          onClick={() =>
                            update.mutate({
                              id: account.id,
                              enabled: !account.enabled,
                            })
                          }
                        >
                          {account.enabled ? <PauseIcon /> : <PlayIcon />}
                          {account.enabled ? "Pause" : "Enable"}
                        </MenuItem>
                        {account.provider === "codex" && (
                          <MenuItem onClick={() => setReset(account)}>
                            <TicketIcon />
                            Reset voucher…
                          </MenuItem>
                        )}
                        <MenuSeparator />
                        <MenuItem
                          variant="destructive"
                          onClick={() => setDeleting(account)}
                        >
                          <TrashIcon />
                          Delete…
                        </MenuItem>
                      </MenuPopup>
                    </Menu>
                  </div>
                </header>
                <AccountUsage view={view} />
              </article>
            );
          })}
        {!catalog.isPending && !accounts.length && (
          <p>Add an account, sign in, then include it in a group.</p>
        )}
      </div>
      {!!catalog.data?.adoption && (
        <details className={styles.migration}>
          <summary>Migration from existing CLI logins</summary>
          <p>
            Imported once on first startup. Original CLI files are unchanged;
            logout and deletion here never re-import them.
          </p>
          {Object.entries(catalog.data.adoption).map(([key, value]) => (
            <p key={key}>
              {key}: {value.status}
              {value.message ? ` · ${value.message}` : ""}
            </p>
          ))}
        </details>
      )}
      <header className={styles.heading}>
        <div>
          <h2>Account groups</h2>
          <p>
            Models select a same-provider group. Changes apply to the next Run.
          </p>
        </div>
        <Button variant="outline" onClick={() => setGroup("new")}>
          Add group
        </Button>
      </header>
      <div className={styles.rows}>
        {catalog.data?.groups.map((item) => (
          <article key={item.id} className={styles.row}>
            <div className={styles.identity}>
              <strong>{item.name}</strong>
              <span>
                {item.provider} ·{" "}
                {item.strategy === "weighted"
                  ? "Weighted balance"
                  : "Ordered consumption"}{" "}
                · {item.accounts?.length ?? 0} accounts
                {item.allow_credits
                  ? " · Credits allowed"
                  : " · Included allowance only"}
              </span>
              <div className={styles.members}>
                {item.accounts?.map((member) => {
                  const view = accounts.find(
                    (entry) => entry.account.id === member.account,
                  );
                  return (
                    <span key={member.account}>
                      {view?.account.name ?? "Deleted account"}
                      {compactAllowance(view?.usage.usage) &&
                        ` · ${compactAllowance(view?.usage.usage)}`}
                      {item.strategy === "weighted"
                        ? ` ×${member.weight ?? 1}`
                        : ""}
                    </span>
                  );
                })}
              </div>
            </div>
            <Button variant="ghost" onClick={() => setGroup(item)}>
              Edit group
            </Button>
          </article>
        ))}
        {!catalog.isPending && !catalog.data?.groups.length && (
          <p>No groups yet. Create one to combine accounts.</p>
        )}
      </div>
      <ModalFrame
        open={creating || !!editing}
        onOpenChange={(open) => {
          if (!open) {
            setCreating(false);
            setEditing(null);
          }
        }}
        title={editing ? "Rename account" : "Add subscription account"}
        closeLabel="Cancel"
        footer={
          <Button
            loading={create.isPending || update.isPending}
            disabled={!name.trim()}
            onClick={() =>
              editing
                ? update.mutate({ id: editing.id, name: name.trim() })
                : create.mutate()
            }
          >
            {editing ? "Save name" : "Create & sign in"}
          </Button>
        }
      >
        <div className={styles.form}>
          <ErrorNotice error={create.error || update.error} />
          {!editing && (
            <ChoiceField
              label="Provider"
              value={provider}
              onValueChange={(value) => setProvider(value as "codex" | "grok")}
              options={[
                { value: "codex", label: "Codex" },
                { value: "grok", label: "Grok" },
              ]}
            />
          )}
          <TextField label="Account name" value={name} onChange={setName} />
        </div>
      </ModalFrame>
      <ModalFrame
        open={!!login}
        onOpenChange={(open) => {
          if (!open) setLogin(null);
        }}
        title={login?.name ?? "Sign in"}
        closeLabel="Done"
      >
        {login && renderLogin(login)}
      </ModalFrame>
      <ModalFrame
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title={`Delete ${deleting?.name ?? "account"}?`}
        description="Removes the managed credentials and stops future requests from using this account, including captured groups. Original CLI files remain unchanged."
        closeLabel="Cancel"
        footer={
          <Button
            variant="destructive"
            loading={remove.isPending}
            onClick={() => deleting && remove.mutate(deleting)}
          >
            Delete account
          </Button>
        }
      >
        <ErrorNotice error={remove.error} />
      </ModalFrame>
      {reset && <ResetVoucher account={reset} close={() => setReset(null)} />}
      {group && (
        <GroupEditor
          initial={group === "new" ? undefined : group}
          accounts={accounts}
          close={() => setGroup(null)}
        />
      )}
    </section>
  );
}

function ResetVoucher({
  account,
  close,
}: {
  account: Account;
  close: () => void;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const [credit, setCredit] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [requestId] = useState(() => crypto.randomUUID());
  const vouchers = useQuery({
    queryKey: ["reset-vouchers", account.id],
    queryFn: () =>
      result(
        client.GET("/api/subscriptions/accounts/{account_id}/reset-vouchers", {
          params: { path: { account_id: account.id } },
        }),
      ),
    retry: false,
  });
  const redeem = useMutation({
    mutationFn: () =>
      result(
        client.POST("/api/subscriptions/accounts/{account_id}/reset", {
          params: { path: { account_id: account.id } },
          body: {
            account_id: vouchers.data!.account_id,
            credit_id: credit,
            redeem_request_id: requestId,
          },
        }),
      ),
    retry: false,
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ["subscriptions"] });
    },
  });
  const available = availableVouchers(vouchers.data?.reset_credits);
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
      title={`Reset voucher · ${account.name}`}
      description="Vouchers reset included allowance. They are separate from rechargeable credits and are never used automatically."
      closeLabel="Close"
      footer={
        !redeem.isSuccess && (
          <Button
            disabled={!credit || !vouchers.data || redeem.isPending}
            loading={redeem.isPending}
            onClick={() => (confirming ? redeem.mutate() : setConfirming(true))}
          >
            {confirming ? "Confirm redeem voucher" : "Review redemption"}
          </Button>
        )
      }
    >
      <div className={styles.form}>
        <ErrorNotice error={vouchers.error || redeem.error} />
        {vouchers.isPending && (
          <p role="status">Checking available vouchers…</p>
        )}
        {vouchers.data?.reset_unavailable && (
          <p>{vouchers.data.reset_unavailable}</p>
        )}
        {vouchers.data && <VoucherSummary usage={vouchers.data} />}
        {vouchers.data?.reset_credits && !available.length && (
          <p>No available reset vouchers.</p>
        )}
        {!!available.length && (
          <ChoiceField
            label="Voucher"
            value={credit}
            disabled={confirming || redeem.isSuccess}
            onValueChange={setCredit}
            options={available.map((item) => ({
              value: item.id,
              label: item.title ?? item.reset_type,
              hint: [
                item.description,
                item.expires_at
                  ? `Expires ${quotaTime(Date.parse(item.expires_at))}`
                  : "Expiry not provided",
              ]
                .filter(Boolean)
                .join(" · "),
            }))}
          />
        )}
        {confirming && !redeem.isSuccess && (
          <p>
            Redeem the selected voucher for <strong>{account.name}</strong> (
            {vouchers.data?.account_id}) now? This consumes the voucher.
          </p>
        )}
        {redeem.isSuccess && (
          <p role="status">
            {redeem.data.code.replaceAll("_", " ")} ·{" "}
            {redeem.data.windows_reset} windows reset
          </p>
        )}
      </div>
    </ModalFrame>
  );
}

function GroupEditor({
  initial,
  accounts,
  close,
}: {
  initial?: Group;
  accounts: AccountView[];
  close: () => void;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const [draft, setDraft] = useState<Group>(
    () =>
      initial ?? {
        schema_version: "1",
        kind: "subscription_group",
        id: `group-${crypto.randomUUID().slice(0, 8)}`,
        name: "New group",
        provider: "codex",
        strategy: "ordered",
        allow_credits: false,
        accounts: [],
      },
  );
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const sources = useQuery({
    queryKey: ["configuration-sources"],
    queryFn: () => result(client.GET("/api/configuration/sources")),
  });
  const source = sources.data?.sources.find(
    (item) =>
      item.resource_ids.includes(draft.id) &&
      item.resource_kind === "subscription_group",
  );
  const relativePath =
    source?.relative_path ?? `subscription_groups/${draft.id}.yaml`;
  const writable = !initial || !!source?.writable;
  const saved = () => {
    void queries.invalidateQueries();
    close();
  };
  const save = useMutation({
    mutationFn: () =>
      result(
        client.PUT("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: relativePath } },
          body: { content: JSON.stringify(draft, null, 2) },
        }),
      ),
    onSuccess: saved,
  });
  const remove = useMutation({
    mutationFn: () =>
      result(
        client.DELETE("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: relativePath } },
        }),
      ),
    onSuccess: saved,
  });
  const members = draft.accounts ?? [];
  const setMembers = (value: typeof members) =>
    setDraft({ ...draft, accounts: value });
  function move(index: number, offset: number) {
    const next = [...members];
    const other = index + offset;
    [next[index], next[other]] = [next[other], next[index]];
    setMembers(next);
  }
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
      title={initial ? `Edit ${initial.name}` : "New account group"}
      description="Members and policy are captured for each Run. Pausing, logout, deletion, and refreshed usage take effect at the next request."
      closeLabel="Cancel"
      footer={
        <div className={styles.actions}>
          {initial && (
            <Button
              variant="ghost"
              disabled={!writable}
              loading={remove.isPending}
              onClick={() =>
                deleteConfirm ? remove.mutate() : setDeleteConfirm(true)
              }
            >
              {deleteConfirm ? "Confirm delete group" : "Delete group…"}
            </Button>
          )}
          <Button
            loading={save.isPending}
            disabled={!draft.name.trim() || !writable || sources.isPending}
            onClick={() => save.mutate()}
          >
            Save group
          </Button>
        </div>
      }
    >
      <div className={styles.form}>
        <ErrorNotice error={sources.error || save.error || remove.error} />
        <TextField
          label="Group name"
          value={draft.name}
          onChange={(name) => setDraft({ ...draft, name })}
        />
        <ChoiceField
          label="Provider"
          value={draft.provider}
          disabled={!!initial}
          onValueChange={(provider) =>
            setDraft({
              ...draft,
              provider: provider as "codex" | "grok",
              accounts: [],
              allow_credits: false,
            })
          }
          options={[
            { value: "codex", label: "Codex" },
            { value: "grok", label: "Grok" },
          ]}
        />
        <ChoiceField
          label="Scheduling"
          value={draft.strategy ?? "ordered"}
          onValueChange={(strategy) =>
            setDraft({ ...draft, strategy: strategy as "ordered" | "weighted" })
          }
          options={[
            {
              value: "ordered",
              label: "Ordered consumption",
              hint: "Use accounts in order, moving on when unavailable or exhausted.",
            },
            {
              value: "weighted",
              label: "Weighted balance",
              hint: "Spread Runs by weight; keep the selected account while eligible.",
            },
          ]}
        />
        {draft.provider === "codex" && (
          <ChoiceField
            label="Rechargeable credits"
            value={draft.allow_credits ? "allow" : "deny"}
            onValueChange={(value) =>
              setDraft({ ...draft, allow_credits: value === "allow" })
            }
            options={[
              { value: "deny", label: "Included allowance only" },
              {
                value: "allow",
                label: "Allow credits after included allowance",
                hint: "Best-effort client scheduling; small overshoot is possible.",
              },
            ]}
          />
        )}
        <FormField label="Members in selection order">
          <div className={styles.form}>
            {members.map((member, index) => (
              <div className={styles.member} key={member.account}>
                <span>
                  {accounts.find((item) => item.account.id === member.account)
                    ?.account.name ?? "Deleted account"}
                </span>
                {draft.strategy === "weighted" && (
                  <Input
                    aria-label={`Weight for member ${index + 1}`}
                    type="number"
                    min={1}
                    max={1000}
                    value={member.weight ?? 1}
                    onChange={(event) =>
                      setMembers(
                        members.map((item, i) =>
                          i === index
                            ? { ...item, weight: Number(event.target.value) }
                            : item,
                        ),
                      )
                    }
                  />
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  Up
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={index === members.length - 1}
                  onClick={() => move(index, 1)}
                >
                  Down
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setMembers(members.filter((_, i) => i !== index))
                  }
                >
                  Remove
                </Button>
              </div>
            ))}
            {!members.length && (
              <p>No members. This group cannot run until you add an account.</p>
            )}
            <ChoiceField
              label="Add member"
              value=""
              onValueChange={(account) => {
                if (account) setMembers([...members, { account, weight: 1 }]);
              }}
              options={accounts
                .filter(
                  ({ account }) =>
                    account.provider === draft.provider &&
                    !members.some((item) => item.account === account.id),
                )
                .map(({ account }) => ({
                  value: account.id,
                  label: account.name,
                }))}
            />
          </div>
        </FormField>
        {!writable && (
          <p>This group is read-only. Edit its owning configuration source.</p>
        )}
        {deleteConfirm && (
          <p>
            Delete this group? Models explicitly referencing it must be updated
            first.
          </p>
        )}
      </div>
    </ModalFrame>
  );
}
