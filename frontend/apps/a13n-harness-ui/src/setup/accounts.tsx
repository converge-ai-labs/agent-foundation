import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, ModalFrame } from "a13n-ui";
import { ArrowSquareOut, Check, Copy } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, PageHeader, Panel, TextField } from "../shell/ui";
import styles from "../shell/workbench.module.css";
import accountStyles from "./accounts.module.css";

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
}: {
  connection: Schema<"AccountConnection">;
  inline?: boolean;
  onReady?: (ready: boolean) => void;
  onIdentity?: (identity: string) => void;
}) {
  const { provider, label, login_methods: loginMethods } = connection;
  const { client } = useTransport();
  const queries = useQueryClient();
  const [session, setSession] = useState<string | null>(
    () =>
      queries.getQueryData<Schema<"LoginStatus">>(["login", provider])
        ?.session_id ?? null,
  );
  const activeLogin = useQuery({
    queryKey: ["active-login"],
    queryFn: ({ signal }) => result(client.GET("/api/auth/logins", { signal })),
    refetchInterval: (query) => (query.state.data?.session_id ? 2000 : false),
  });
  useEffect(() => {
    const status = activeLogin.data;
    if (status?.provider === provider && status.session_id !== session) {
      queries.setQueryData(["login", provider], status);
      setSession(status.session_id);
    }
  }, [activeLogin.data, provider, queries, session]);
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
    queryKey: ["account", provider],
    queryFn: ({ signal }) =>
      result(
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
      queries.setQueryData(["account", provider], status);
      void queries.invalidateQueries({
        queryKey: ["account-sources", provider],
      });
      void queries.invalidateQueries({ queryKey: ["setup"] });
    },
  });
  const login = useQuery({
    queryKey: ["login", provider],
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
          body: { provider, method, allow_account_switch: allowAccountSwitch },
        }),
      ),
    onSuccess: (status) => {
      setSwitchOpen(false);
      setCallbackUrl("");
      setCallbackSent(false);
      queries.setQueryData(["login", provider], status);
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
      queries.setQueryData(["login", provider], status);
      void queries.invalidateQueries({ queryKey: ["active-login"] });
    },
  });
  const logout = useMutation({
    mutationFn: () =>
      result(
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
      void queries.invalidateQueries({ queryKey: ["account", provider] });
      void queries.invalidateQueries({ queryKey: ["setup"] });
      void queries.invalidateQueries({
        queryKey: ["account-sources", provider],
      });
    }
  }, [login.data?.state, queries, provider]);
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
      await navigator.clipboard.writeText(code);
      setCopiedCode(code);
      setCopyFailed(false);
    } catch {
      setCopiedCode(null);
      setCopyFailed(true);
    }
  }
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
          <p className={accountStyles.accountStatus}>
            {account.isPending
              ? "Checking account…"
              : account.data?.usable
                ? "Connected"
                : ready
                  ? "Account available · refreshes on first use"
                  : "Not connected"}
            {!inline &&
              account.data &&
              ` · ${account.data.source} · ${account.data.expiry}`}
          </p>
          {account.data?.account_id && (
            <p>
              {account.data.account_id} ·{" "}
              {account.data.shared_with_cli
                ? "Shared Copilot CLI file"
                : "Host account"}
            </p>
          )}
          {account.data?.message && <p role="status">{account.data.message}</p>}
          {connection.source_selection && (
            <details className={accountStyles.advanced}>
              <summary>Choose account source</summary>
              <p>
                A selection replaces this server's binding. Missing credentials
                never fall back to another account.
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
                  Find saved accounts
                </Button>
              </div>
              {!sources.isPending && !sources.data?.length && (
                <p>
                  No supported saved accounts found. Connect an account to use
                  native device login.
                </p>
              )}
            </details>
          )}
          {!inline && account.data?.required_action !== "none" && (
            <p>{account.data?.required_action?.replaceAll("_", " ")}</p>
          )}
          <div className={styles.stack}>
            {loginMethods.length > 1 && (
              <details className={accountStyles.advanced}>
                <summary>Advanced login options</summary>
                <ChoiceField
                  label="Login method"
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
                    setMethod(value as "device" | "browser" | "manual_callback")
                  }
                />
                {method === "browser" && (
                  <p>
                    The callback must reach the server's loopback listener. For
                    a remote host or container, paste the complete callback URL
                    for ChatGPT, or use device login where supported.
                  </p>
                )}
              </details>
            )}
            {activeLogin.data?.provider &&
              activeLogin.data.provider !== provider && (
                <p role="status">
                  A {activeLogin.data.provider} login is in progress. Return to
                  that connection to finish or cancel it.
                </p>
              )}
            <div className={styles.actions}>
              <Button
                loading={start.isPending}
                disabled={
                  !!activeLogin.data?.session_id || !loginMethods.length
                }
                onClick={() => start.mutate(false)}
              >
                {account.data?.usable ? "Reconnect account" : "Connect account"}
              </Button>
              <Button variant="outline" onClick={() => void account.refetch()}>
                Refresh status
              </Button>
              {account.data?.availability === "available" && (
                <Button variant="outline" onClick={() => setLogoutOpen(true)}>
                  Log out account
                </Button>
              )}
            </div>
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
                      Could not copy automatically. Select and copy the code
                      above.
                    </p>
                  )}
                </li>
              )}
            </ol>
          )}
          {login.data?.message && <p>{login.data.message}</p>}
          {active &&
            provider === "chatgpt" &&
            login.data?.state === "waiting" && (
              <details
                className={accountStyles.advanced}
                open={method === "manual_callback" || undefined}
              >
                <summary>Callback cannot reach this server?</summary>
                <p>
                  After authorizing, copy the complete URL from the browser
                  address bar, even if the browser shows a connection error. It
                  contains a one-time code; do not share it.
                </p>
                <TextField
                  label="Complete callback URL"
                  type="password"
                  value={callbackUrl}
                  onChange={setCallbackUrl}
                  disabled={callbackSent}
                />
                <Button
                  loading={callback.isPending}
                  disabled={!callbackUrl.trim() || callbackSent}
                  onClick={() => callback.mutate()}
                >
                  Complete sign-in
                </Button>
                {callbackSent && (
                  <p role="status">Callback received. Completing sign-in…</p>
                )}
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
        Shared by everyone on this server. Connecting does not verify model
        access.
      </p>
      <ModalFrame
        open={switchOpen}
        onOpenChange={setSwitchOpen}
        title="Allow account switching?"
        description="The next login may replace this provider's saved account on the server for everyone using it."
        closeLabel="Cancel"
        footer={
          <Button loading={start.isPending} onClick={() => start.mutate(true)}>
            Allow switch and log in
          </Button>
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
            ? "Clear the selected Host's ChatGPT tokens for everyone and request remote revocation. The registration is retained for future sign-in. Codex credentials are not changed."
            : account.data?.source_id?.startsWith("native:")
              ? "Remove the selected Host-owned credentials for everyone using this server. Other saved accounts remain intact. This does not revoke GitHub authorization or delete Models and Agents."
              : "Remove the selected local account credentials. This also affects the official CLI and other Hosts sharing this account store. Other accounts remain intact. This does not revoke authorization, delete Models or Agents, or disconnect this browser."
        }
        closeLabel="Cancel"
        footer={
          <Button
            variant="destructive"
            loading={logout.isPending}
            onClick={() => logout.mutate()}
          >
            Log out
          </Button>
        }
      >
        <ErrorNotice error={logout.error} />
      </ModalFrame>
    </>
  );
  return inline ? (
    <div className={styles.stack}>{content}</div>
  ) : (
    <Panel title={`${label} account`}>{content}</Panel>
  );
}

export function CredentialKeys() {
  const { client } = useTransport();
  const queries = useQueryClient();
  const [reference, setReference] = useState("key-primary");
  const [secret, setSecret] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [saved, setSaved] = useState("");
  const keys = useQuery({
    queryKey: ["keys"],
    queryFn: ({ signal }) => result(client.GET("/api/auth/keys", { signal })),
  });
  const save = useMutation({
    mutationFn: (body: Schema<"ApiKeyInput">) =>
      result(client.PUT("/api/auth/keys", { body })),
    onSuccess: (_, body) => {
      setSecret("");
      setSaved(`Saved ${body.credential_ref}.`);
      void queries.invalidateQueries({ queryKey: ["keys"] });
    },
  });
  const remove = useMutation({
    mutationFn: (reference: string) =>
      result(
        client.DELETE("/api/auth/keys/{reference}", {
          params: { path: { reference } },
        }),
      ),
    onSuccess: () => {
      setDeleting(null);
      void queries.invalidateQueries({ queryKey: ["keys"] });
    },
  });
  return (
    <Panel title="Model API keys">
      <p>
        Keys are stored on the server. Model resources use a credential
        reference; these keys are separate from your browser's instance access
        key.
      </p>
      <ErrorNotice error={keys.error || save.error || remove.error} />
      <form
        className={styles.stack}
        onSubmit={(event) => {
          event.preventDefault();
          setSaved("");
          save.mutate({ credential_ref: reference, key: secret });
        }}
      >
        <div className={styles.formGrid}>
          <TextField
            label="Saved key name"
            value={reference}
            onChange={setReference}
          />
          <TextField
            label="Provider API key"
            type="password"
            value={secret}
            onChange={setSecret}
          />
        </div>
        <Button
          type="submit"
          loading={save.isPending}
          disabled={!secret || !reference}
        >
          Save key
        </Button>
      </form>
      {saved && <p role="status">{saved}</p>}
      <div className={styles.resourceList}>
        {keys.data?.map((key) => (
          <div className={styles.resourceRow} key={key.credential_ref}>
            <strong>{key.credential_ref}</strong>
            <Button
              variant="ghost"
              onClick={() => setDeleting(key.credential_ref)}
            >
              Remove
            </Button>
          </div>
        ))}
      </div>
      <ModalFrame
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title="Remove saved key?"
        description="Models referencing this credential will need a replacement key before their next run."
        closeLabel="Cancel"
        footer={
          <Button
            variant="destructive"
            loading={remove.isPending}
            onClick={() => remove.mutate(deleting!)}
          >
            Remove key
          </Button>
        }
      >
        <p>{deleting}</p>
        <ErrorNotice error={remove.error} />
      </ModalFrame>
    </Panel>
  );
}
export function AccountsPage() {
  const { client } = useTransport();
  const choices = useQuery({
    queryKey: ["model-choices"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/models/choices", { signal })),
  });
  return (
    <>
      <PageHeader title="Accounts & API keys" />
      <div className={styles.twoColumns}>
        <ErrorNotice error={choices.error} />
        {choices.data?.connections?.map(
          (connection) =>
            connection.account && (
              <ProviderAccount
                key={connection.id}
                connection={connection.account}
              />
            ),
        )}
      </div>
      <CredentialKeys />
    </>
  );
}
