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
  provider,
  inline = false,
  onReady,
}: {
  provider: "codex" | "grok";
  inline?: boolean;
  onReady?: (ready: boolean) => void;
}) {
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

  const [method, setMethod] = useState<"device" | "browser">("device");
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
      queries.setQueryData(["login", provider], status);
      setSession(status.session_id);
      queries.setQueryData(["active-login"], status);
    },
    onSettled: () => {
      void queries.invalidateQueries({ queryKey: ["active-login"] });
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
    }
  }, [login.data?.state, queries, provider]);
  const ready =
    !!account.data?.usable ||
    (account.data?.availability === "available" &&
      account.data.required_action === "refresh");
  useEffect(() => {
    onReady?.(ready);
  }, [onReady, ready]);
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
          logout.error
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
          {!inline && account.data?.required_action !== "none" && (
            <p>{account.data?.required_action?.replaceAll("_", " ")}</p>
          )}
          <div className={styles.stack}>
            <details className={accountStyles.advanced}>
              <summary>Advanced login options</summary>
              <ChoiceField
                label="Login method"
                value={method}
                options={[
                  {
                    value: "device",
                    label: "Device code (recommended for remote servers)",
                  },
                  { value: "browser", label: "Browser callback on the server" },
                ]}
                onValueChange={(value) =>
                  setMethod(value as "device" | "browser")
                }
              />
              {method === "browser" && (
                <p>
                  The callback must reach the server's loopback listener. For a
                  remote host or container, use device login where supported.
                </p>
              )}
            </details>
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
                disabled={!!activeLogin.data?.session_id}
                onClick={() => start.mutate(false)}
              >
                {account.data?.usable ? "Reconnect account" : "Connect account"}
              </Button>
              <Button variant="outline" onClick={() => void account.refetch()}>
                Refresh status
              </Button>
              {account.data?.usable && (
                <Button variant="outline" onClick={() => setLogoutOpen(true)}>
                  Disconnect account
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
                    <span>{url}</span>
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
        description="Remove the local account credentials. This does not disconnect your browser from this Harness UI instance."
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
    <Panel title={provider === "codex" ? "Codex account" : "Grok account"}>
      {content}
    </Panel>
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
  return (
    <>
      <PageHeader title="Accounts & API keys" />
      <div className={styles.twoColumns}>
        <ProviderAccount provider="codex" />
        <ProviderAccount provider="grok" />
      </div>
      <CredentialKeys />
    </>
  );
}
