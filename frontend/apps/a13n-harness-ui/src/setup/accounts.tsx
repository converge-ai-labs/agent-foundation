import {
  SettingsChoice as ChoiceField,
  SettingsSection,
  SettingsTextField as TextField,
} from "../shell/settings";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, ModalFrame } from "a13n-ui";
import { ArrowSquareOut, Check, Copy } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, PageHeader, Panel } from "../shell/ui";
import styles from "../shell/workbench.module.css";
import accountStyles from "./accounts.module.css";
import { SubscriptionAccounts, AccountGroups } from "./subscriptions";
import { CredentialKeys } from "./credential-keys";
import { ChatGPTAllowance, CopilotAllowance } from "./provider-usage";
import { copyText } from "../platform";

function safeLoginUrl(value: string | null | undefined): string | undefined {
  if (!value) return;
  try {
    const url = new URL(value);
    if (url.protocol === "https:" || url.protocol === "http:") return url.href;
  } catch {
    /* Invalid provider URL. */
  }
}
export function ProviderAccount({
  connection,
  inline = false,
  onReady,
  onIdentity,
  accountId,
}: {
  connection: Schema<"AccountConnection">;
  inline?: boolean;
  accountId?: string;
  onReady?: (ready: boolean) => void;
  onIdentity?: (identity: string) => void;
}) {
  const { provider, label, login_methods: loginMethods } = connection;
  const nativeAccountId =
    accountId ??
    (provider === "codex" || provider === "grok"
      ? `acct-${provider}-default`
      : undefined);
  const accountKey = nativeAccountId ?? provider;
  const { client } = useTransport();
  const queries = useQueryClient();
  const [session, setSession] = useState<string | null>(
    () =>
      queries.getQueryData<Schema<"LoginStatus">>(["login", accountKey])
        ?.session_id ?? null,
  );
  const activeLogin = useQuery({
    queryKey: ["active-login"],
    queryFn: ({ signal }) => result(client.GET("/api/auth/logins", { signal })),
    refetchInterval: (query) => (query.state.data?.session_id ? 2000 : false),
  });
  useEffect(() => {
    const status = activeLogin.data;
    if (
      status?.provider === provider &&
      (!nativeAccountId || status.account_id === nativeAccountId) &&
      status.session_id !== session
    ) {
      queries.setQueryData(["login", accountKey], status);
      setSession(status.session_id);
    }
  }, [
    activeLogin.data,
    provider,
    accountKey,
    nativeAccountId,
    queries,
    session,
  ]);
  const [copiedCode, setCopiedCode] = useState<string | null>(null);
  const [copyFailed, setCopyFailed] = useState(false);

  const [method, setMethod] = useState<
    "device" | "browser" | "manual_callback"
  >(provider === "chatgpt" ? "browser" : (loginMethods[0] ?? "device"));
  const [callbackUrl, setCallbackUrl] = useState("");
  const [callbackSent, setCallbackSent] = useState(false);
  const [logoutOpen, setLogoutOpen] = useState(false);
  const [switchOpen, setSwitchOpen] = useState(false);
  const account = useQuery({
    queryKey: ["account", accountKey],
    queryFn: ({ signal }) =>
      nativeAccountId
        ? result(
            client.GET("/api/subscriptions/accounts/{account_id}", {
              params: { path: { account_id: nativeAccountId } },
              signal,
            }),
          )
        : result(
            client.GET("/api/auth/accounts/{provider}", {
              params: { path: { provider } },
              signal,
            }),
          ),
  });
  const sources = useQuery({
    queryKey: ["account-sources", provider],
    enabled: !!connection.source_selection,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/auth/accounts/{provider}/sources", {
          params: { path: { provider } },
          signal,
        }),
      ),
  });
  const select = useMutation({
    mutationFn: (selection: Schema<"AccountSelection">) =>
      result(
        client.PUT("/api/auth/accounts/{provider}/selection", {
          params: { path: { provider } },
          body: selection,
        }),
      ),
    onSuccess: (status) => {
      queries.setQueryData(["account", accountKey], status);
      void queries.invalidateQueries({
        queryKey: ["account-sources", provider],
      });
      void queries.invalidateQueries({ queryKey: ["setup"] });
    },
  });
  const login = useQuery({
    queryKey: ["login", accountKey],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/auth/logins/{session_id}", {
          params: { path: { session_id: session! } },
          signal,
        }),
      ),
    enabled: !!session,
    refetchInterval: (query) =>
      !query.state.error &&
      (!query.state.data ||
        ["starting", "waiting"].includes(query.state.data.state ?? "starting"))
        ? 2000
        : false,
  });
  const start = useMutation({
    mutationFn: (allowAccountSwitch: boolean) =>
      result(
        client.POST("/api/auth/logins", {
          body: {
            provider,
            method,
            account_id: nativeAccountId,
            allow_account_switch: allowAccountSwitch,
          },
        }),
      ),
    onSuccess: (status) => {
      setSwitchOpen(false);
      setCallbackUrl("");
      setCallbackSent(false);
      queries.setQueryData(["login", accountKey], status);
      setSession(status.session_id);
      queries.setQueryData(["active-login"], status);
    },
    onSettled: () => {
      void queries.invalidateQueries({ queryKey: ["active-login"] });
    },
  });
  const callback = useMutation({
    mutationFn: () =>
      result(
        client.POST("/api/auth/logins/{session_id}/callback", {
          params: { path: { session_id: session! } },
          body: { callback_url: callbackUrl },
        }),
      ),
    onSuccess: () => {
      setCallbackUrl("");
      setCallbackSent(true);
    },
  });
  const cancel = useMutation({
    mutationFn: () =>
      result(
        client.DELETE("/api/auth/logins/{session_id}", {
          params: { path: { session_id: session! } },
        }),
      ),
    onSuccess: (status) => {
      queries.setQueryData(["login", accountKey], status);
      void queries.invalidateQueries({ queryKey: ["active-login"] });
    },
  });
  const logout = useMutation({
    mutationFn: () =>
      nativeAccountId
        ? result(
            client.POST("/api/subscriptions/accounts/{account_id}/logout", {
              params: { path: { account_id: nativeAccountId } },
            }),
          )
        : result(
            client.DELETE("/api/auth/accounts/{provider}", {
              params: { path: { provider } },
            }),
          ),
    onSuccess: () => {
      setLogoutOpen(false);
      setSession(null);
      void queries.invalidateQueries();
    },
  });
  useEffect(() => {
    if (login.data?.state === "succeeded") {
      void queries.invalidateQueries({ queryKey: ["subscriptions"] });
      void queries.invalidateQueries({ queryKey: ["account", accountKey] });
      void queries.invalidateQueries({ queryKey: ["setup"] });
      void queries.invalidateQueries({
        queryKey: ["account-sources", provider],
      });
    }
  }, [login.data?.state, queries, provider, accountKey]);
  const ready =
    !!account.data?.usable ||
    (account.data?.availability === "available" &&
      account.data.required_action === "refresh");
  useEffect(() => {
    onReady?.(ready && !select.isPending && !logout.isPending);
  }, [onReady, ready, select.isPending, logout.isPending]);
  const identity = `${account.data?.source_id ?? ""}:${account.data?.account_id ?? ""}`;
  useEffect(() => {
    onIdentity?.(identity);
  }, [onIdentity, identity]);
  const active =
    !!session &&
    !login.error &&
    (!login.data ||
      ["starting", "waiting"].includes(login.data.state ?? "starting"));
  const url = safeLoginUrl(login.data?.verification_url);
  const code = login.data?.user_code;
  useEffect(() => {
    setCopiedCode(null);
    setCopyFailed(false);
  }, [session, code]);
  useEffect(() => {
    if (!active) setCallbackUrl("");
  }, [active]);
  async function copyCode() {
    if (!code) return;
    try {
      await copyText(code);
      setCopiedCode(code);
      setCopyFailed(false);
    } catch {
      setCopiedCode(null);
      setCopyFailed(true);
    }
  }
  const accountActions = (
    <div className={styles.actions}>
      <Button
        variant="outline"
        loading={start.isPending}
        disabled={!!activeLogin.data?.session_id || !loginMethods.length}
        onClick={() => start.mutate(false)}
      >
        {account.data?.usable ? "Reconnect account" : "Connect account"}
      </Button>
      <Button variant="ghost" onClick={() => void account.refetch()}>
        Refresh status
      </Button>
      {account.data?.availability === "available" && (
        <Button variant="ghost" onClick={() => setLogoutOpen(true)}>
          Log out account
        </Button>
      )}
    </div>
  );
  const content = (
    <>
      <ErrorNotice
        error={
          account.error ||
          start.error ||
          login.error ||
          cancel.error ||
          logout.error ||
          sources.error ||
          select.error ||
          callback.error
        }
      />
      {!active && (
        <>
          <div className={accountStyles.accountHeader}>
            <div className={accountStyles.identity}>
              <p className={accountStyles.accountStatus}>
                {account.isPending
                  ? "Checking account…"
                  : account.data?.usable
                    ? "Connected"
                    : ready
                      ? "Account available · refreshes on first use"
                      : "Not connected"}
                {account.data?.availability === "available" &&
                  ` · ${account.data.source}${account.data.expiry === "unknown" ? "" : ` · ${account.data.expiry}`}`}
              </p>
              {account.data?.account_id && (
                <p>
                  Account: {account.data.account_id} ·{" "}
                  {account.data.shared_with_cli
                    ? `Shared with ${label} CLI`
                    : "Host account"}
                </p>
              )}
              {account.data?.expires_at && (
                <p className={accountStyles.help}>
                  Session expires{" "}
                  {new Date(account.data.expires_at).toLocaleString()}
                  {account.data.required_action === "refresh"
                    ? " · refreshes on first use"
                    : ""}
                </p>
              )}
              {account.data?.message && (
                <p role="status">{account.data.message}</p>
              )}
            </div>
            {accountActions}
          </div>
          {!inline && provider === "copilot" && (
            <CopilotAllowance
              key={identity}
              identity={identity}
              ready={ready}
            />
          )}
          {!inline && provider === "chatgpt" && <ChatGPTAllowance />}
          {connection.source_selection && (
            <details className={accountStyles.advanced}>
              <summary>Account source</summary>
              <p>
                Choosing an account replaces this server's selection. If its
                credentials are missing, no other account is used.
              </p>
              <div className={styles.actions}>
                {sources.data?.map((candidate) => (
                  <Button
                    key={`${candidate.selection.source}:${candidate.selection.account_id}`}
                    variant="outline"
                    disabled={
                      candidate.selected ||
                      select.isPending ||
                      !!activeLogin.data?.session_id
                    }
                    onClick={() => select.mutate(candidate.selection)}
                  >
                    {candidate.label}
                    {candidate.selected ? " · selected" : ""}
                  </Button>
                ))}
                <Button
                  variant="outline"
                  onClick={() => void sources.refetch()}
                >
                  Find accounts
                </Button>
              </div>
              {!sources.isPending && !sources.data?.length && (
                <p>No saved accounts found. Connect an account to sign in.</p>
              )}
            </details>
          )}
          <div className={styles.stack}>
            {loginMethods.length > 1 && (
              <details className={accountStyles.advanced}>
                <summary>Advanced login options</summary>
                <SettingsSection>
                  <ChoiceField
                    label="Login method"
                    description={
                      method === "browser"
                        ? "The browser must reach this server. For remote servers, use a device code or paste the full ChatGPT callback URL."
                        : undefined
                    }
                    value={method}
                    options={loginMethods.map((method) => ({
                      value: method,
                      label:
                        method === "device"
                          ? "Device code (recommended for remote servers)"
                          : method === "manual_callback"
                            ? "Paste the complete callback URL"
                            : "Automatic browser callback",
                    }))}
                    onValueChange={(value) =>
                      setMethod(
                        value as "device" | "browser" | "manual_callback",
                      )
                    }
                  />
                </SettingsSection>
              </details>
            )}
            {activeLogin.data?.provider &&
              activeLogin.data.provider !== provider && (
                <p role="status">
                  A {activeLogin.data.provider} login is in progress. Return to
                  that connection to finish or cancel it.
                </p>
              )}
          </div>
        </>
      )}
      {session && (
        <section aria-label="Provider login" className={accountStyles.login}>
          <div className={accountStyles.loginHeader}>
            <h3>
              {active ? "Finish connecting your account" : "Account login"}
            </h3>
            <span role="status" className={accountStyles.loginStatus}>
              Login: {login.data?.state ?? "starting"}
            </span>
          </div>
          {active && (url || code) && (
            <ol className={accountStyles.loginSteps}>
              {url && (
                <li>
                  <span>Open the verification page</span>
                  <a
                    className={accountStyles.verificationLink}
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <span>
                      {provider === "chatgpt" ? "Sign in with ChatGPT" : url}
                    </span>
                    <ArrowSquareOut size={18} aria-hidden="true" />
                    <span className="sr-only"> (opens in a new tab)</span>
                  </a>
                </li>
              )}
              {code && (
                <li>
                  <span>Enter this device code</span>
                  <div className={accountStyles.deviceCodeRow}>
                    <strong className={accountStyles.deviceCode}>{code}</strong>
                    <Button variant="outline" onClick={() => void copyCode()}>
                      {copiedCode === code ? (
                        <Check aria-hidden="true" />
                      ) : (
                        <Copy aria-hidden="true" />
                      )}
                      {copiedCode === code ? "Copied" : "Copy code"}
                    </Button>
                  </div>
                  {copyFailed && (
                    <p role="status">
                      Couldn't copy. Select the code above and copy it manually.
                    </p>
                  )}
                </li>
              )}
            </ol>
          )}
          {login.data?.message &&
            !(
              active &&
              provider === "chatgpt" &&
              login.data.state === "waiting"
            ) && <p>{login.data.message}</p>}
          {active &&
            provider === "chatgpt" &&
            login.data?.state === "waiting" && (
              <details
                className={accountStyles.advanced}
                open={method === "manual_callback" || undefined}
              >
                <summary>Callback can't reach this server?</summary>
                <div className={styles.stack}>
                  <p>
                    {login.data.message ??
                      "After signing in, paste the full URL from your browser, even if that page shows an error. It contains a private one-time code."}
                  </p>
                  <FormField label="Complete callback URL">
                    <Input
                      type="password"
                      autoComplete="off"
                      spellCheck={false}
                      maxLength={16384}
                      value={callbackUrl}
                      onChange={(event) => setCallbackUrl(event.target.value)}
                      disabled={callback.isPending || callbackSent}
                      onKeyDown={(event) => {
                        if (event.key !== "Enter") return;
                        event.preventDefault();
                        if (
                          callbackUrl.trim() &&
                          !callback.isPending &&
                          !callbackSent
                        )
                          callback.mutate();
                      }}
                    />
                  </FormField>
                  <Button
                    variant="outline"
                    loading={callback.isPending}
                    disabled={!callbackUrl.trim() || callbackSent}
                    onClick={() => callback.mutate()}
                  >
                    Complete sign-in
                  </Button>
                  {callbackSent && (
                    <p role="status">Callback received. Completing sign-in…</p>
                  )}
                </div>
              </details>
            )}
          {login.data?.error_code ===
            "account_switch_confirmation_required" && (
            <Button variant="outline" onClick={() => setSwitchOpen(true)}>
              Switch account…
            </Button>
          )}
          {active && (
            <Button
              variant="outline"
              loading={cancel.isPending}
              onClick={() => cancel.mutate()}
            >
              Cancel login
            </Button>
          )}
        </section>
      )}
      <p className={accountStyles.help}>
        This account is shared by everyone on this server.
      </p>
      <ModalFrame
        open={switchOpen}
        onOpenChange={setSwitchOpen}
        title="Allow account switching?"
        description="Signing in may replace this provider's account for everyone on this server."
        closeLabel="Close"
        footer={
          <>
            <Button
              variant="outline"
              disabled={start.isPending}
              onClick={() => setSwitchOpen(false)}
            >
              Cancel
            </Button>
            <Button
              loading={start.isPending}
              onClick={() => start.mutate(true)}
            >
              Switch and log in
            </Button>
          </>
        }
      >
        <ErrorNotice error={start.error} />
      </ModalFrame>
      <ModalFrame
        open={logoutOpen}
        onOpenChange={setLogoutOpen}
        title={`Log out of ${provider}?`}
        description={
          provider === "chatgpt"
            ? "Sign everyone out of ChatGPT on this server and request token revocation. Registration is kept for future sign-in. Codex stays signed in."
            : nativeAccountId
              ? "Sign out of this saved account? The original CLI login and other accounts are kept."
              : account.data?.source_id?.startsWith("native:")
                ? "Sign everyone out of this account on this server. Other accounts, models and agents are kept. GitHub authorization stays active."
                : "Sign out of this local account, including the official CLI and other servers sharing it. Other accounts, models, agents and this browser login are kept. Provider authorization stays active."
        }
        closeLabel="Close"
        footer={
          <>
            <Button
              variant="outline"
              disabled={logout.isPending}
              onClick={() => setLogoutOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              loading={logout.isPending}
              onClick={() => logout.mutate()}
            >
              Log out
            </Button>
          </>
        }
      >
        <ErrorNotice error={logout.error} />
      </ModalFrame>
    </>
  );
  return inline ? (
    <div className={accountStyles.inlineAccount}>{content}</div>
  ) : (
    <Panel title={`${label} account`}>{content}</Panel>
  );
}

export function AccountsPage() {
  const [params] = useSearchParams();
  const tab = params.get("tab") ?? "accounts";
  const { client } = useTransport();
  const choices = useQuery({
    queryKey: ["model-choices"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/models/choices", { signal })),
  });
  return (
    <div className={accountStyles.page}>
      <PageHeader
        title="Accounts & API keys"
        description="Connect the accounts and API keys your models use."
      />
      <nav
        className={`${accountStyles.tabs} a13n-scrollbar`}
        aria-label="Account settings"
      >
        {[
          ["accounts", "Subscription accounts"],
          ["groups", "Account groups"],
          ["keys", "API keys"],
        ].map(([value, label]) => (
          <Link
            key={value}
            to={`?tab=${value}`}
            aria-current={tab === value ? "page" : undefined}
          >
            {label}
          </Link>
        ))}
      </nav>
      <ErrorNotice error={choices.error} />
      {tab !== "groups" && tab !== "keys" && (
        <SubscriptionAccounts
          connections={
            choices.data?.connections?.flatMap((item) =>
              item.account ? [item.account] : [],
            ) ?? []
          }
          renderConnection={(connection) => (
            <ProviderAccount
              key={connection.provider}
              connection={connection}
              inline
            />
          )}
          renderLogin={(account) => {
            const connection = choices.data?.connections?.find(
              (item) => item.account?.provider === account.provider,
            )?.account;
            return connection ? (
              <ProviderAccount
                key={account.id}
                connection={connection}
                accountId={account.id}
                inline
              />
            ) : null;
          }}
        />
      )}
      {tab === "groups" && <AccountGroups />}
      {tab === "keys" && (
        <CredentialKeys connections={choices.data?.connections ?? []} />
      )}
    </div>
  );
}
