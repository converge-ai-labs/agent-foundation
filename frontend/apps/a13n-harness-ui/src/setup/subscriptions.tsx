import { useTranslation } from "../i18n";
import {
  SettingsChoice as ChoiceField,
  SettingsTextField as TextField,
  SettingsSection,
} from "../shell/settings";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  BrandIcon,
  Button,
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
  ArrowLeftIcon,
  ArrowsClockwiseIcon,
  CaretRightIcon,
  DotsThreeIcon,
  KeyIcon,
  PauseIcon,
  PencilSimpleIcon,
  PlayIcon,
  PlusIcon,
  TicketIcon,
  TrashIcon,
  WalletIcon,
} from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { EmptyList, ErrorNotice, SearchField } from "../shell/ui";
import { TextField as ModalTextField } from "../shell/ui";
import styles from "./subscriptions.module.css";
import layout from "../configuration/settings.module.css";
import {
  ChatGPTAllowance,
  CopilotAllowance,
  GrokAllowance,
} from "./provider-usage";
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
  const { t } = useTranslation();
  const remaining = Math.max(0, Math.min(100, 100 - window.used_percent));
  return (
    <div className={styles.meter}>
      <div className={styles.meterHeading}>
        <span>{label}</span>
        <span>
          {t("{{percent}}% left", { percent: Math.round(remaining) })}
        </span>
      </div>
      <meter
        min={0}
        max={100}
        value={remaining}
        aria-label={t("{{label}} remaining", { label })}
      />
      <span
        className={styles.quotaDetail}
        title={
          window.reset_at > 0 ? quotaTime(window.reset_at * 1000) : undefined
        }
      >
        {window.reset_at > 0
          ? t("Resets {{time}}", {
              time: quotaTime(window.reset_at * 1000, true),
            })
          : t("Reset time unavailable")}
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
  const { t } = useTranslation();
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
        {!windows.length && <span>{t("Usage windows unavailable")}</span>}
      </div>
      {limits && (!limits.allowed || limits.limit_reached) && (
        <span>{t("Included allowance unavailable")}</span>
      )}
    </div>
  );
}

function VoucherSummary({
  usage,
}: {
  usage: Schema<"CodexUsage"> | null | undefined;
}) {
  const { t } = useTranslation();
  const vouchers = voucherSummary(usage);
  return (
    <div className={styles.voucherSummary}>
      <span className={styles.summaryItem}>
        <TicketIcon size={15} aria-hidden />
        {vouchers.count === null
          ? t("Reset vouchers - Unavailable")
          : t("Reset vouchers - {{count}} available", {
              count: vouchers.count,
            })}
      </span>
      {vouchers.expiresAt !== null && (
        <span className={styles.quotaDetail}>
          {t("Expires {{time}}", { time: quotaTime(vouchers.expiresAt) })}
        </span>
      )}
      {vouchers.count !== null &&
        vouchers.count > 0 &&
        vouchers.expiresAt === null && <span>{t("Expiry not provided")}</span>}
      {usage?.reset_unavailable && (
        <span>{t("Voucher details could not be refreshed")}</span>
      )}
    </div>
  );
}

export function AccountUsage({
  view,
  onVouchers,
}: {
  view: AccountView;
  onVouchers?: () => void;
}) {
  const { t } = useTranslation();
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
              ? `${limit.limit_name} - ${limit.normal_model_slug}`
              : limit.limit_name
          }
        />
      ))}
      {monthly && (
        <div className={styles.quotaGroup}>
          <WindowUsage window={monthly} label={t("Monthly credit limit")} />
          <span>
            {t("{{used}} / {{limit}} credits used - {{remaining}} remaining", {
              used: monthly.used,
              limit: monthly.limit,
              remaining: monthly.remaining,
            })}
          </span>
        </div>
      )}
      {usage?.spend_control?.reached && (
        <span>{t("Workspace spending limit reached")}</span>
      )}
      <div className={styles.balanceSummary}>
        <div className={styles.balanceCell}>
          <VoucherSummary usage={usage} />
          {onVouchers && (
            <Button variant="ghost" size="sm" onClick={onVouchers}>
              {t("View vouchers")}
              <CaretRightIcon />
            </Button>
          )}
        </div>
        {usage?.credits && (
          <div className={styles.balanceCell}>
            <span className={styles.summaryItem}>
              <WalletIcon size={15} aria-hidden />
              {t("Rechargeable credits")}
            </span>
            <strong
              className={styles.balanceValue}
              title={usage.credits.balance ?? undefined}
            >
              {usage.credits.unlimited
                ? t("Unlimited")
                : usage.credits.balance != null
                  ? Number(usage.credits.balance).toLocaleString(undefined, {
                      maximumFractionDigits: 2,
                    })
                  : usage.credits.has_credits
                    ? t("Available")
                    : t("None")}
            </strong>
          </div>
        )}
      </div>
      <UsageFreshness snapshot={view.usage} />
    </div>
  );
}

function UsageFreshness({ snapshot }: { snapshot: Schema<"UsageSnapshot"> }) {
  const { t } = useTranslation();
  return (
    <span className={styles.quotaDetail}>
      {snapshot.error ? t("Refresh failed - ") : ""}
      {snapshot.observed_at
        ? t("Updated {{time}}", {
            time: quotaTime(snapshot.observed_at * 1000),
          })
        : t("Not refreshed yet")}
    </span>
  );
}

function SubscriptionAccountRow({
  view,
  children,
  refreshing,
  onRefresh,
  onSignIn,
  onEnable,
  onVouchers,
}: {
  view: AccountView;
  children: ReactNode;
  refreshing: boolean;
  onRefresh: () => void;
  onSignIn: () => void;
  onEnable: () => void;
  onVouchers?: () => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const { account, authentication } = view;
  const usageAvailable = authentication.usable || !!view.usage.usage;
  const allowance = compactAllowance(view.usage.usage);
  const usage = view.usage.usage;
  return (
    <article className={styles.accountCard}>
      <header className={styles.accountHeader}>
        <div className={styles.identity}>
          <strong>{account.name}</strong>
          <span className={styles.accountMeta}>
            <span
              className={styles.status}
              data-connected={account.enabled && authentication.usable}
            >
              {!account.enabled
                ? t("Paused")
                : authentication.usable
                  ? t("Connected")
                  : t("Sign-in required")}
            </span>
            {usage && "plan_type" in usage && usage.plan_type !== "unknown" && (
              <span className={styles.plan}>{usage.plan_type}</span>
            )}
          </span>
        </div>
        <div className={styles.cardActions}>
          {usageAvailable && (
            <Button
              variant="ghost"
              size="sm"
              aria-label={t("Usage for {{name}}", { name: account.name })}
              aria-expanded={expanded}
              onClick={() => setExpanded(!expanded)}
            >
              <span className={styles.allowanceSummary}>
                {allowance || t("Usage")}
              </span>
              <CaretRightIcon
                className={expanded ? styles.expanded : undefined}
              />
            </Button>
          )}
          {!account.enabled ? (
            <Button variant="outline" size="sm" onClick={onEnable}>
              {t("Enable")}
            </Button>
          ) : !authentication.usable ? (
            <Button variant="outline" size="sm" onClick={onSignIn}>
              {t("Sign in")}
            </Button>
          ) : null}
          {children}
        </div>
      </header>
      {expanded && usageAvailable && (
        <div className={styles.usageDetails}>
          <div className={styles.usageToolbar}>
            <span>{t("Usage & allowance")}</span>
            <Button
              variant="ghost"
              size="sm"
              loading={refreshing}
              onClick={onRefresh}
            >
              <ArrowsClockwiseIcon />
              {t("Refresh")}
            </Button>
          </div>
          <AccountUsage view={view} onVouchers={onVouchers} />
        </div>
      )}
    </article>
  );
}

export function SubscriptionAccounts({
  renderLogin,
  connections,
  renderConnection,
}: {
  renderLogin: (account: Account) => ReactNode;
  connections: Schema<"AccountConnection">[];
  renderConnection: (connection: Schema<"AccountConnection">) => ReactNode;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const catalog = useSubscriptions();
  const activeLogin = useQuery({
    queryKey: ["active-login"],
    queryFn: ({ signal }) => result(client.GET("/api/auth/logins", { signal })),
    refetchInterval: (query) => (query.state.data?.session_id ? 2000 : false),
  });
  const previousLogin = useRef<string | null>(null);
  useEffect(() => {
    const session = activeLogin.data?.session_id ?? null;
    if (previousLogin.current && previousLogin.current !== session) {
      // Login can complete while its dialog is closed. Refresh the list even
      // when the provider-specific completion observer is no longer mounted.
      for (const key of [
        "account",
        "account-sources",
        "subscriptions",
        "setup",
      ])
        void queries.invalidateQueries({ queryKey: [key] });
    }
    previousLogin.current = session;
  }, [activeLogin.data?.session_id, queries]);
  const [creating, setCreating] = useState(false);
  const [provider, setProvider] =
    useState<Schema<"AccountConnection">["provider"]>("codex");
  const [connectionLogin, setConnectionLogin] =
    useState<Schema<"AccountConnection"> | null>(null);
  const singleConnections = connections.filter(
    (item) => item.provider !== "codex" && item.provider !== "grok",
  );
  const singleAccounts = useQueries({
    queries: singleConnections.map((connection) => ({
      queryKey: ["account", connection.provider],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        result(
          client.GET("/api/auth/accounts/{provider}", {
            params: { path: { provider: connection.provider } },
            signal,
          }),
        ),
    })),
  });
  const connected = singleConnections.flatMap((connection, index) => {
    const status = singleAccounts[index].data;
    return status?.availability === "available" || status?.account_id
      ? [{ connection, status }]
      : [];
  });
  const namedProvider = provider === "codex" || provider === "grok";
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<Account | null>(null);
  const [login, setLogin] = useState<Account | null>(null);
  const [deleting, setDeleting] = useState<Account | null>(null);
  const [reset, setReset] = useState<Account | null>(null);
  const [filter, setFilter] = useState("");
  const invalidate = () =>
    queries.invalidateQueries({ queryKey: ["subscriptions"] });
  const create = useMutation({
    mutationFn: (provider: "codex" | "grok") =>
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
  const pendingAccount = accounts.find(
    ({ account }) => account.id === activeLogin.data?.account_id,
  )?.account;
  const pendingConnection = singleConnections.find(
    (item) => item.provider === activeLogin.data?.provider,
  );
  return (
    <section className={styles.section} aria-label={t("Subscription accounts")}>
      <div className={styles.listToolbar}>
        <SearchField
          label={t("Search accounts")}
          value={filter}
          onChange={setFilter}
        />
        <Button
          onClick={() => {
            setName("");
            setProvider(connections[0]?.provider ?? "codex");
            create.reset();
            setCreating(true);
          }}
        >
          <PlusIcon aria-hidden="true" />
          {t("Add account")}
        </Button>
      </div>
      <ErrorNotice
        error={
          catalog.error ||
          update.error ||
          refresh.error ||
          singleAccounts.find((item) => item.error)?.error
        }
      />
      {activeLogin.data?.session_id &&
        (pendingAccount || pendingConnection) && (
          <div className={styles.row} role="status">
            <span>
              {t("Sign-in in progress - {{name}}", {
                name: pendingAccount?.name ?? pendingConnection?.label,
              })}
            </span>
            <Button
              variant="outline"
              onClick={() => {
                if (pendingAccount) setLogin(pendingAccount);
                else if (pendingConnection)
                  setConnectionLogin(pendingConnection);
              }}
            >
              {t("Continue sign-in")}
            </Button>
          </div>
        )}
      {catalog.isPending && <p role="status">{t("Loading accounts…")}</p>}
      <div className={styles.rows}>
        {(["codex", "grok"] as const).map((provider) => {
          const providerAccounts = accounts.filter(
            ({ account }) =>
              account.provider === provider &&
              `${account.name} ${account.provider}`
                .toLowerCase()
                .includes(filter.toLowerCase()),
          );
          if (!providerAccounts.length) return null;
          return (
            <section
              key={provider}
              className={styles.providerSection}
              aria-label={t("{{provider}} accounts", {
                provider: provider === "codex" ? "Codex" : "Grok",
              })}
            >
              <header className={styles.providerHeading}>
                <BrandIcon
                  identity={provider === "codex" ? "openai" : "xai"}
                  size={20}
                />
                <h3>{provider === "codex" ? "Codex" : "Grok"}</h3>
                <span>
                  {providerAccounts.length === 1
                    ? t("1 account")
                    : t("{{count}} accounts", {
                        count: providerAccounts.length,
                      })}
                </span>
              </header>
              <div className={styles.accountList}>
                {providerAccounts.map((view) => {
                  const { account } = view;
                  return (
                    <SubscriptionAccountRow
                      key={account.id}
                      view={view}
                      refreshing={
                        refresh.isPending && refresh.variables === account.id
                      }
                      onRefresh={() => refresh.mutate(account.id)}
                      onSignIn={() => setLogin(account)}
                      onEnable={() =>
                        update.mutate({ id: account.id, enabled: true })
                      }
                      onVouchers={
                        account.provider === "codex"
                          ? () => setReset(account)
                          : undefined
                      }
                    >
                      <Menu>
                        <MenuTrigger
                          render={
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={t("Manage account")}
                            />
                          }
                        >
                          <DotsThreeIcon />
                        </MenuTrigger>
                        <MenuPopup align="end">
                          <MenuItem onClick={() => setLogin(account)}>
                            <KeyIcon />
                            {t("Sign-in & logout")}
                          </MenuItem>
                          <MenuItem
                            onClick={() => {
                              setName(account.name);
                              setEditing(account);
                            }}
                          >
                            <PencilSimpleIcon />
                            {t("Rename")}
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
                            {account.enabled ? t("Pause") : t("Enable")}
                          </MenuItem>
                          {account.provider === "codex" && (
                            <MenuItem onClick={() => setReset(account)}>
                              <TicketIcon />
                              {t("Reset voucher…")}
                            </MenuItem>
                          )}
                          <MenuItem
                            disabled={
                              refresh.isPending &&
                              refresh.variables === account.id
                            }
                            onClick={() => refresh.mutate(account.id)}
                          >
                            <ArrowsClockwiseIcon />
                            {t("Refresh usage")}
                          </MenuItem>
                          <MenuSeparator />
                          <MenuItem
                            variant="destructive"
                            onClick={() => setDeleting(account)}
                          >
                            <TrashIcon />
                            {t("Delete…")}
                          </MenuItem>
                        </MenuPopup>
                      </Menu>
                    </SubscriptionAccountRow>
                  );
                })}
              </div>
            </section>
          );
        })}
        {connected
          .filter(({ connection, status }) =>
            `${connection.label} ${status.account_id ?? ""}`
              .toLowerCase()
              .includes(filter.toLowerCase()),
          )
          .map(({ connection, status }) => (
            <section
              key={connection.provider}
              className={styles.providerSection}
              aria-label={t("{{provider}} accounts", {
                provider: connection.label,
              })}
            >
              <header className={styles.providerHeading}>
                <BrandIcon
                  identity={
                    connection.provider === "copilot" ? "github" : "openai"
                  }
                  size={20}
                />
                <h3>{connection.label}</h3>
                <span>{t("1 account")}</span>
              </header>
              <div className={styles.accountList}>
                <article className={styles.accountCard}>
                  <header className={styles.accountHeader}>
                    <div className={styles.accountIdentity}>
                      <div className={styles.identity}>
                        <strong>
                          {status.account_id ?? t("Server account")}
                        </strong>
                        <span className={styles.accountMeta}>
                          <span
                            className={styles.status}
                            data-connected={status.usable}
                          >
                            {status.usable
                              ? t("Connected")
                              : status.required_action === "refresh"
                                ? t("Refreshes on first use")
                                : t("Sign-in required")}
                          </span>
                        </span>
                      </div>
                    </div>
                    <Button
                      variant="ghost"
                      onClick={() => setConnectionLogin(connection)}
                    >
                      {t("Manage account")}
                    </Button>
                  </header>
                  <details className={styles.legacyUsage}>
                    <summary>{t("Usage & allowance")}</summary>
                    {connection.provider === "copilot" ? (
                      <CopilotAllowance
                        identity={`${status.source_id ?? ""}:${status.account_id ?? ""}`}
                        ready={
                          status.usable || status.required_action === "refresh"
                        }
                      />
                    ) : (
                      <ChatGPTAllowance />
                    )}
                  </details>
                </article>
              </div>
            </section>
          ))}
        {!catalog.isPending &&
          !accounts.length &&
          !connected.length &&
          !singleAccounts.some((item) => item.isPending) && (
            <EmptyList>
              {t("No accounts connected. Add an account to get started.")}
            </EmptyList>
          )}
      </div>
      {!!catalog.data?.adoption && (
        <details className={`${styles.migration} ${layout.disclosure}`}>
          <summary>{t("Import existing CLI logins")}</summary>
          <p>
            {t(
              "Imported once on first startup. CLI files stay unchanged. Signing out or deleting an account won't import it again.",
            )}
          </p>
          {Object.entries(catalog.data.adoption).map(([key, value]) => (
            <p key={key}>
              {key}: {value.status}
              {value.message ? ` - ${value.message}` : ""}
            </p>
          ))}
        </details>
      )}
      <ModalFrame
        bottomStickOnMobile
        open={creating || !!editing}
        onOpenChange={(open) => {
          if (!open) {
            setCreating(false);
            setEditing(null);
          }
        }}
        title={editing ? t("Rename account") : t("Add account")}
        closeLabel={t("Close")}
        footer={
          <>
            <Button
              variant="outline"
              disabled={create.isPending || update.isPending}
              onClick={() => {
                setCreating(false);
                setEditing(null);
              }}
            >
              {t("Cancel")}
            </Button>
            <Button
              loading={create.isPending || update.isPending}
              disabled={
                editing
                  ? !name.trim()
                  : !connections.some((item) => item.provider === provider) ||
                    (namedProvider && !name.trim())
              }
              onClick={() => {
                if (editing)
                  update.mutate({ id: editing.id, name: name.trim() });
                else if (provider === "codex" || provider === "grok")
                  create.mutate(provider);
                else {
                  const connection = connections.find(
                    (item) => item.provider === provider,
                  );
                  if (connection) {
                    setCreating(false);
                    setConnectionLogin(connection);
                  }
                }
              }}
            >
              {editing ? t("Save name") : t("Continue")}
            </Button>
          </>
        }
      >
        <div className={styles.form}>
          <ErrorNotice error={create.error || update.error} />
          {!editing && (
            <ChoiceField
              label={t("Provider")}
              layout="stacked"
              value={provider}
              onValueChange={(value) =>
                setProvider(value as Schema<"AccountConnection">["provider"])
              }
              options={connections.map((item) => ({
                value: item.provider,
                label: item.label,
              }))}
            />
          )}
          {editing || namedProvider ? (
            <ModalTextField
              label={t("Account name")}
              value={name}
              onChange={setName}
            />
          ) : (
            <p>
              {t(
                "This provider shares one account across the server. Continue to manage it.",
              )}
            </p>
          )}
        </div>
      </ModalFrame>
      <ModalFrame
        bottomStickOnMobile
        open={!!connectionLogin}
        onOpenChange={(open) => {
          if (!open) setConnectionLogin(null);
        }}
        title={connectionLogin?.label ?? t("Sign in")}
        closeLabel={t("Done")}
      >
        {connectionLogin && renderConnection(connectionLogin)}
      </ModalFrame>
      <ModalFrame
        bottomStickOnMobile
        open={!!login}
        onOpenChange={(open) => {
          if (!open) setLogin(null);
        }}
        title={login?.name ?? t("Sign in")}
        closeLabel={t("Done")}
      >
        {login && renderLogin(login)}
      </ModalFrame>
      <ModalFrame
        bottomStickOnMobile
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title={t("Delete {{name}}?", { name: deleting?.name ?? t("account") })}
        description={t(
          "Remove this saved account? Future requests can't use it, even through saved groups. Original CLI files are kept.",
        )}
        closeLabel={t("Close")}
        footer={
          <>
            <Button
              variant="outline"
              disabled={remove.isPending}
              onClick={() => setDeleting(null)}
            >
              {t("Cancel")}
            </Button>
            <Button
              variant="destructive"
              loading={remove.isPending}
              onClick={() => deleting && remove.mutate(deleting)}
            >
              {t("Delete account")}
            </Button>
          </>
        }
      >
        <ErrorNotice error={remove.error} />
      </ModalFrame>
      {reset && <ResetVoucher account={reset} close={() => setReset(null)} />}
    </section>
  );
}

export function AccountGroups() {
  const { t } = useTranslation();
  const catalog = useSubscriptions();
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState("");
  const id = params.get("group");
  const accounts = catalog.data?.accounts ?? [];
  const group = catalog.data?.groups.find((item) => item.id === id);
  const groups =
    catalog.data?.groups.filter((item) =>
      `${item.name} ${item.provider}`
        .toLowerCase()
        .includes(filter.toLowerCase()),
    ) ?? [];
  if (id && (group || id === "new"))
    return (
      <GroupEditor
        key={id}
        initial={group}
        accounts={accounts}
        close={() => setParams({ tab: "groups" })}
      />
    );
  return (
    <section className={styles.section} aria-label={t("Account groups")}>
      <ErrorNotice error={catalog.error} />
      {catalog.isPending && <p role="status">{t("Loading groups…")}</p>}
      {id && !catalog.isPending && !group && (
        <p role="status">{t("Group not found.")}</p>
      )}
      <div className={styles.listToolbar}>
        {!catalog.data?.groups.length && (
          <p className={styles.help}>
            {t("Combine accounts from the same provider.")}
          </p>
        )}
        {!!catalog.data?.groups.length && (
          <SearchField
            label={t("Search groups")}
            value={filter}
            onChange={setFilter}
          />
        )}
        <Button onClick={() => setParams({ tab: "groups", group: "new" })}>
          <PlusIcon aria-hidden="true" />
          {t("Add group")}
        </Button>
      </div>
      <div className={styles.rows}>
        {groups.map((item) => (
          <article key={item.id} className={styles.row}>
            <div className={styles.identity}>
              <strong>{item.name}</strong>
              <span>
                {item.provider} -{" "}
                {item.strategy === "weighted" ? t("By weight") : t("In order")}{" "}
                -{" "}
                {t("{{count}} accounts", { count: item.accounts?.length ?? 0 })}
                {item.allow_credits
                  ? ` - ${t("Credits allowed")}`
                  : ` - ${t("Included allowance only")}`}
              </span>
              <div className={styles.members}>
                {item.accounts?.map((member) => {
                  const view = accounts.find(
                    (entry) => entry.account.id === member.account,
                  );
                  return (
                    <span key={member.account}>
                      {view?.account.name ?? t("Deleted account")}
                      {compactAllowance(view?.usage.usage) &&
                        ` - ${compactAllowance(view?.usage.usage)}`}
                      {item.strategy === "weighted"
                        ? ` ×${member.weight ?? 1}`
                        : ""}
                    </span>
                  );
                })}
              </div>
            </div>
            <Button
              variant="ghost"
              onClick={() => setParams({ tab: "groups", group: item.id })}
            >
              {t("Manage group")}
            </Button>
          </article>
        ))}
        {!catalog.isPending && !groups.length && (
          <p className={layout.emptySection}>
            {catalog.data?.groups.length
              ? t("No matching groups.")
              : t("No account groups yet.")}
          </p>
        )}
      </div>
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
  const { t } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const [selection, setSelection] = useState<{
    voucher: Schema<"ResetCredit">;
    accountId: string;
    requestId: string;
  } | null>(null);
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
            account_id: selection!.accountId,
            credit_id: selection!.voucher.id,
            redeem_request_id: selection!.requestId,
          },
        }),
      ),
    retry: false,
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ["subscriptions"] });
    },
  });
  const available = availableVouchers(vouchers.data?.reset_credits);
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (selection) cancelRef.current?.focus();
  }, [selection]);
  return (
    <ModalFrame
      bottomStickOnMobile
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
      title={
        redeem.isSuccess
          ? t("Voucher result - {{name}}", { name: account.name })
          : selection
            ? t("Use voucher - {{name}}?", { name: account.name })
            : t("Reset vouchers - {{name}}", { name: account.name })
      }
      description={
        redeem.isSuccess
          ? undefined
          : selection
            ? t(
                "This consumes one voucher to reset this account’s included allowance.",
              )
            : t(
                "Use a voucher to reset your included allowance. Earliest expiry first.",
              )
      }
      closeLabel={t("Close")}
      footer={
        selection &&
        !redeem.isSuccess && (
          <>
            <Button
              variant="ghost"
              ref={cancelRef}
              disabled={!redeem.isIdle}
              onClick={() => setSelection(null)}
            >
              {t("Cancel")}
            </Button>
            <Button
              disabled={redeem.isPending}
              loading={redeem.isPending}
              onClick={() => redeem.mutate()}
            >
              {redeem.isError ? t("Retry use voucher") : t("Use voucher")}
            </Button>
          </>
        )
      }
    >
      <div className={styles.form}>
        <ErrorNotice error={vouchers.error || redeem.error} />
        {vouchers.isPending && (
          <p role="status">{t("Checking available vouchers…")}</p>
        )}
        {vouchers.data?.reset_unavailable && (
          <p>{vouchers.data.reset_unavailable}</p>
        )}
        {!selection && !redeem.isSuccess && (
          <>
            {vouchers.data?.reset_credits && !available.length && (
              <p>{t("No available reset vouchers.")}</p>
            )}
            {!!available.length && (
              <ul
                className={styles.voucherList}
                aria-label={t("Available reset vouchers")}
              >
                {available.map((item) => (
                  <li key={item.id} className={styles.voucherRow}>
                    <VoucherDetails voucher={item} />
                    <Button
                      variant="outline"
                      onClick={() =>
                        setSelection({
                          voucher: item,
                          accountId: vouchers.data!.account_id,
                          requestId: crypto.randomUUID(),
                        })
                      }
                    >
                      {t("Use")}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        {selection && !redeem.isSuccess && (
          <>
            <div className={styles.voucherRow}>
              <VoucherDetails voucher={selection.voucher} />
            </div>
            <p className={styles.quotaDetail}>
              {t("Account: {{name}} - {{id}}", {
                name: account.name,
                id: selection.accountId,
              })}
            </p>
          </>
        )}
        {redeem.isSuccess && (
          <p role="status">
            {redeem.data.code.replaceAll("_", " ")} -{" "}
            {t("{{count}} windows reset", { count: redeem.data.windows_reset })}
          </p>
        )}
      </div>
    </ModalFrame>
  );
}

function VoucherDetails({ voucher }: { voucher: Schema<"ResetCredit"> }) {
  const { t } = useTranslation();
  return (
    <div className={styles.voucherDetails}>
      <strong>{voucher.title || t("Reset voucher")}</strong>
      {voucher.description && <p>{voucher.description}</p>}
      <span>
        {voucher.expires_at
          ? t("Expires {{time}}", {
              time: quotaTime(Date.parse(voucher.expires_at)),
            })
          : t("Expiry not provided")}
      </span>
    </div>
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
  const { t } = useTranslation();
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
  const availableMembers = accounts.filter(
    ({ account }) =>
      account.provider === draft.provider &&
      !members.some((item) => item.account === account.id),
  );
  const setMembers = (value: typeof members) =>
    setDraft({ ...draft, accounts: value });
  function move(index: number, offset: number) {
    const next = [...members];
    const other = index + offset;
    [next[index], next[other]] = [next[other], next[index]];
    setMembers(next);
  }
  const busy = save.isPending || remove.isPending;
  return (
    <section className={styles.groupDetail} aria-label={t("Group details")}>
      <Button
        variant="ghost"
        className={styles.back}
        onClick={close}
        disabled={busy}
      >
        <ArrowLeftIcon />
        {t("Account groups")}
      </Button>
      <header className={styles.heading}>
        <div>
          <h2>{initial ? initial.name : t("New account group")}</h2>
          <p>{t("Choose accounts for future runs.")}</p>
        </div>
      </header>
      <ErrorNotice error={sources.error || save.error || remove.error} />
      <fieldset className={styles.groupFields} disabled={!writable || busy}>
        <SettingsSection>
          <TextField
            label={t("Group name")}
            value={draft.name}
            onChange={(name) => setDraft({ ...draft, name })}
          />
          <ChoiceField
            label={t("Provider")}
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
            label={t("Scheduling")}
            value={draft.strategy ?? "ordered"}
            onValueChange={(strategy) =>
              setDraft({
                ...draft,
                strategy: strategy as "ordered" | "weighted",
              })
            }
            options={[
              {
                value: "ordered",
                label: t("In order"),
              },
              {
                value: "weighted",
                label: t("By weight"),
              },
            ]}
          />
          {draft.provider === "codex" && (
            <ChoiceField
              label={t("Rechargeable credits")}
              value={draft.allow_credits ? "allow" : "deny"}
              onValueChange={(value) =>
                setDraft({ ...draft, allow_credits: value === "allow" })
              }
              options={[
                { value: "deny", label: t("Included allowance only") },
                {
                  value: "allow",
                  label: t("Allow extra credits"),
                },
              ]}
            />
          )}
          {draft.provider === "codex" && draft.allow_credits && (
            <p className={styles.help}>
              {t(
                "Uses credits after the included allowance. Usage may go slightly over the limit.",
              )}
            </p>
          )}
        </SettingsSection>
        <SettingsSection
          title={t("Accounts")}
          description={
            draft.strategy === "weighted"
              ? t(
                  "Higher weights get more runs. A run keeps its account while it remains eligible.",
                )
              : t(
                  "Tries accounts from top to bottom. Put your preferred account first.",
                )
          }
        >
          <FormField
            className={styles.memberField}
            hideLabel
            label={
              draft.strategy === "weighted"
                ? t("Members and weights")
                : t("Members in selection order")
            }
          >
            <div className={styles.form}>
              {members.map((member, index) => (
                <div className={styles.member} key={member.account}>
                  <span className={styles.memberNumber}>{index + 1}</span>
                  <span>
                    {accounts.find((item) => item.account.id === member.account)
                      ?.account.name ?? t("Deleted account")}
                  </span>
                  {draft.strategy === "weighted" && (
                    <Input
                      aria-label={t("Weight for member {{number}}", {
                        number: index + 1,
                      })}
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
                    {t("Up")}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={index === members.length - 1}
                    onClick={() => move(index, 1)}
                  >
                    {t("Down")}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setMembers(members.filter((_, i) => i !== index))
                    }
                  >
                    {t("Remove")}
                  </Button>
                </div>
              ))}
              {!members.length && (
                <p>
                  {availableMembers.length
                    ? t("Choose the accounts this group can use.")
                    : t(
                        "Connect an account in Subscription accounts before using this group.",
                      )}
                </p>
              )}
              {availableMembers.length > 0 && (
                <ChoiceField
                  label={t("Add member")}
                  placeholder={t("Choose an account")}
                  value=""
                  onValueChange={(account) => {
                    if (account)
                      setMembers([...members, { account, weight: 1 }]);
                  }}
                  options={availableMembers.map(({ account }) => ({
                    value: account.id,
                    label: account.name,
                  }))}
                />
              )}
            </div>
          </FormField>
          {!writable && (
            <p>
              {t(
                "This group is read-only here. Edit its configuration file instead.",
              )}
            </p>
          )}
        </SettingsSection>
      </fieldset>
      <div className={styles.editorFooter}>
        <p className={styles.help}>
          {JSON.stringify(draft) !== JSON.stringify(initial)
            ? t("Unsaved changes")
            : t("Saved configuration")}
        </p>
        <div className={styles.actions}>
          {initial && (
            <Button
              variant="ghost"
              disabled={!writable || save.isPending}
              loading={remove.isPending}
              onClick={() => setDeleteConfirm(true)}
            >
              {t("Delete group…")}
            </Button>
          )}
          <Button variant="outline" disabled={busy} onClick={close}>
            {t("Cancel")}
          </Button>
          <Button
            loading={save.isPending}
            disabled={
              !draft.name.trim() ||
              !writable ||
              sources.isPending ||
              remove.isPending
            }
            onClick={() => save.mutate()}
          >
            {t("Save")}
          </Button>
        </div>
      </div>
      <ModalFrame
        bottomStickOnMobile
        open={deleteConfirm}
        onOpenChange={(open) => {
          if (!busy) setDeleteConfirm(open);
        }}
        title={t("Delete account group?")}
        description={t(
          "Update models that reference this group before deleting it.",
        )}
        closeLabel={t("Close")}
        footer={
          <>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setDeleteConfirm(false)}
            >
              {t("Cancel")}
            </Button>
            <Button
              variant="destructive"
              loading={remove.isPending}
              disabled={!writable || save.isPending}
              onClick={() => remove.mutate()}
            >
              {t("Delete group")}
            </Button>
          </>
        }
      >
        <ErrorNotice error={remove.error} />
      </ModalFrame>
    </section>
  );
}
