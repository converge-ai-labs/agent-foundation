import { useEffect, useRef, useState } from "react";
import { api, result, message, type Model } from "./client";

type Selection = Model<"SetupSelection">;
type Connection = "subscription" | "api_key" | "later";
export function Setup({
  status,
  reload,
  close,
  setApplying,
}: {
  status: Model<"SetupStatus">;
  reload: (signal?: AbortSignal) => Promise<Model<"SetupStatus">>;
  close: () => void;
  setApplying: (pending: boolean) => void;
}) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [connection, setConnection] = useState<Connection>("subscription");
  const [providers, setProviders] = useState<("codex" | "grok")[]>(
    status.providers.filter((p) => p.selected).map((p) => p.provider),
  );
  const [route, setRoute] = useState("");
  const [keyEnv, setKeyEnv] = useState("OPENAI_API_KEY");
  const [model, setModel] = useState<Selection["codex_model"]>("gpt-5.6-terra");
  const [review, setReview] = useState(true);
  const [instructions, setInstructions] = useState("");
  const [agent, setAgent] = useState(status.default_agent ?? "");
  const [project, setProject] = useState(
    status.default_project ?? "project-local",
  );
  const [path, setPath] = useState(status.suggested_project_path ?? ".");
  const [environment, setEnvironment] = useState<
    Selection["environment_profile"]
  >(
    status.environment_profile === "environment-sandbox"
      ? "environment-sandbox"
      : "environment-native",
  );
  const [preview, setPreview] = useState<Model<"SetupPreview">>();
  const [readiness, setReadiness] = useState<Model<"EnvironmentReadiness">>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const pending = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    heading.current?.focus();
  }, [step]);
  const selectedProviders = connection === "subscription" ? providers : [];
  const agents: Record<string, string> = { ...status.agents };
  for (const provider of selectedProviders)
    agents[`agent-${provider}`] ??= `${provider} starter`;
  if (connection === "api_key") agents["agent-api-key"] ??= "API key Agent";
  if (!selectedProviders.length && connection !== "api_key")
    agents["agent-default"] ??= "Default Agent — connect a model later";
  const agentIds = Object.keys(agents).join("|");
  useEffect(() => {
    const ids = agentIds.split("|").filter(Boolean);
    if (!ids.includes(agent)) setAgent(ids[0] ?? "agent-default");
  }, [agent, agentIds]);
  const selection: Selection = {
    providers: selectedProviders,
    api_key_model:
      connection === "api_key"
        ? { route, authentication: { kind: "api_key", env: keyEnv } }
        : null,
    instructions,
    default_agent: agent,
    project,
    project_path: path,
    environment_profile: environment,
    shell_review: review,
    codex_model: model,
  };
  const identity = JSON.stringify(selection);
  const roots = status.project_paths?.[project] ?? [path];
  const environmentIdentity = JSON.stringify([environment, roots]);
  useEffect(() => {
    setPreview(undefined);
    setError("");
  }, [identity]);
  useEffect(() => {
    pending.current?.abort();
    setReadiness(undefined);
  }, [environmentIdentity]);

  async function perform(action: "preview" | "apply" | "probe" | "retry") {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    const signal = controller.signal;
    setBusy(action);
    setError("");
    if (action === "probe") setReadiness(undefined);
    if (action === "apply") setApplying(true);
    try {
      if (action === "retry") {
        const next = await reload(signal);
        setProviders(
          next.providers.filter((p) => p.selected).map((p) => p.provider),
        );
      } else if (action === "probe") {
        let passed: Model<"EnvironmentReadiness"> | undefined;
        for (const root of roots) {
          const value = result(
            await api.POST("/api/environments/preflight", {
              body: { profile_id: "environment-sandbox", project_path: root },
              signal,
            }),
          );
          if (signal.aborted) return;
          if (!value.ready) {
            setReadiness(value);
            return;
          }
          passed = value;
        }
        setReadiness(passed);
      } else if (action === "preview") {
        setPreview(
          result(
            await api.POST("/api/setup/preview", { body: selection, signal }),
          ),
        );
      } else if (preview) {
        const value = result(
          await api.POST("/api/setup/apply", {
            body: { selection, expected_generation: preview.generation },
            signal,
          }),
        );

        if (!value.completed) {
          setPreview(undefined);
          setError(
            `${value.error_message} Published files retained: ${value.published_paths.join(", ") || "none"}. Review a fresh preview before retrying.`,
          );
        } else {
          await reload(signal);
          setApplying(false);
          close();
        }
      }
    } catch (failure) {
      if (!signal.aborted) {
        if (action === "apply") {
          setPreview(undefined);
          setError(
            `${message(failure)} Publication may have completed or partially written files. Inspect the configuration directory, then make a new preview; do not blindly repeat Apply.`,
          );
        } else setError(message(failure));
      }
    } finally {
      if (pending.current === controller) setBusy("");
      if (action === "apply") setApplying(false);
    }
  }
  const environmentReady =
    environment === "environment-native" || !!readiness?.ready;
  const connectionReady =
    connection === "api_key"
      ? !!route.trim() && /^[A-Za-z_][A-Za-z0-9_]*$/.test(keyEnv)
      : providers.length > 0 || Object.keys(status.agents).length > 0;
  return (
    <section className="setup">
      <p className="eyebrow">FIRST-USE SETUP</p>
      <h1>Make yourself at home.</h1>
      <p className="muted">
        Connect a model now or later, choose its execution authority, then make
        an Agent yours. Nothing is written until you finish.
      </p>
      <ol className="setup-steps" aria-label="Setup progress">
        {["Model connection", "Execution environment", "Agent"].map(
          (name, i) => (
            <li key={name} aria-current={step === i + 1 ? "step" : undefined}>
              {name}
            </li>
          ),
        )}
      </ol>
      <h2 ref={heading} tabIndex={-1}>
        Step {step} of 3:{" "}
        {step === 1
          ? "Model connection"
          : step === 2
            ? "Execution environment"
            : "Your default Agent"}
      </h2>
      {status.diagnostic && <p role="alert">{status.diagnostic}</p>}
      {step === 1 && (
        <fieldset disabled={!!busy}>
          <legend>How would you like to connect?</legend>
          <label>
            <input
              type="radio"
              name="connection"
              checked={connection === "api_key"}
              onChange={() => setConnection("api_key")}
            />
            API key — BYOK
          </label>
          <label>
            <input
              type="radio"
              name="connection"
              checked={connection === "subscription"}
              onChange={() => setConnection("subscription")}
            />
            Subscription — BYOS
          </label>
          {connection === "subscription" && (
            <>
              <p className="muted">
                Use an existing Codex or Grok login. To add an account, complete
                login in a terminal on the server host, then refresh below.
                In-app browser login is not connected to this wizard yet.
              </p>
              {status.providers.map((provider) => (
                <div key={provider.provider}>
                  <label>
                    <input
                      type="checkbox"
                      checked={providers.includes(provider.provider)}
                      disabled={!provider.available}
                      onChange={(e) =>
                        setProviders(
                          e.target.checked
                            ? [...providers, provider.provider]
                            : providers.filter((p) => p !== provider.provider),
                        )
                      }
                    />
                    {provider.provider} subscription —{" "}
                    {provider.available ? "available locally" : provider.action}
                  </label>
                  {!provider.available && (
                    <p className="muted">
                      <code>a13n-ui auth login {provider.provider}</code>{" "}
                      {provider.diagnostic}
                    </p>
                  )}
                </div>
              ))}
              <button onClick={() => void perform("retry")}>
                Refresh accounts
              </button>
            </>
          )}
          {connection === "api_key" && (
            <>
              <p className="muted">
                Configure an API-key model using a host environment variable.
                This step does not save a key or verify provider access. Set the
                variable before starting Agent UI; a browser's environment is
                not the server environment.
              </p>
              <label>
                Model route
                <input
                  value={route}
                  placeholder="provider:model-name"
                  onChange={(e) => setRoute(e.target.value)}
                />
              </label>
              <label>
                API key environment variable
                <input
                  value={keyEnv}
                  onChange={(e) => setKeyEnv(e.target.value)}
                  autoComplete="off"
                />
              </label>
              <p className="muted">
                Enter the variable name, not the API key. Direct key storage is
                not available in this wizard yet.
              </p>
            </>
          )}
          {connection === "later" && (
            <p>Model connection deferred. You can connect later from Setup.</p>
          )}
        </fieldset>
      )}
      {step === 2 && (
        <fieldset disabled={!!busy}>
          <legend>Where may your Agent work?</legend>
          <label>
            Project
            <select
              value={project}
              onChange={(e) => setProject(e.target.value)}
            >
              {Object.entries({
                "project-local": "Local project",
                ...status.projects,
              }).map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label>
            New local project path
            <input
              value={path}
              disabled={!!status.project_paths?.[project]}
              onChange={(e) => setPath(e.target.value)}
            />
          </label>
          <p className="muted">
            Effective roots: {roots.join(", ")}. Existing Project roots are
            preserved.
          </p>
          <label>
            <input
              type="radio"
              name="setup-environment"
              checked={environment === "environment-sandbox"}
              onChange={() => setEnvironment("environment-sandbox")}
            />
            Sandbox — verify isolation before continuing
          </label>
          <p className="muted">
            Checks the production runtime's filesystem, process and
            denied-network isolation. No system security policy changes.
          </p>
          <label>
            <input
              type="radio"
              name="setup-environment"
              checked={environment === "environment-native"}
              onChange={() => setEnvironment("environment-native")}
            />
            Full Control — no Sandbox
          </label>
          <p className="muted">
            Runs as your host account with ambient filesystem and network
            access. Shell review is not isolation.
          </p>
          {environment === "environment-sandbox" && (
            <button disabled={!path} onClick={() => void perform("probe")}>
              Check Sandbox / Retry
            </button>
          )}
        </fieldset>
      )}
      {step === 2 && readiness && (
        <div role="status" className={readiness.ready ? "notice" : "error"}>
          <strong>{readiness.message}</strong>
          {readiness.instructions?.map((line) => (
            <p key={line}>{line}</p>
          ))}
          <a
            href={readiness.documentation_url}
            target="_blank"
            rel="noreferrer"
          >
            Sandbox documentation
          </a>
          {!readiness.ready && (
            <button
              disabled={!!busy}
              onClick={() => setEnvironment("environment-native")}
            >
              Choose Full Control (no Sandbox)
            </button>
          )}
        </div>
      )}
      {step === 3 && (
        <fieldset disabled={!!busy}>
          <legend>Ready-to-use Agent defaults</legend>
          <label>
            Default agent
            <select value={agent} onChange={(e) => setAgent(e.target.value)}>
              {Object.entries(agents).map(([id, name]) => (
                <option value={id} key={id}>
                  {name} ({id})
                </option>
              ))}
            </select>
          </label>
          <p className="muted">
            Existing Agents are preserved unchanged. New defaults affect new
            conversations only. An unconfigured Agent cannot send until you
            connect a model.
          </p>
          {selectedProviders.includes("codex") && (
            <label>
              Codex model
              <select
                value={model}
                onChange={(e) =>
                  setModel(e.target.value as Selection["codex_model"])
                }
              >
                <option value="gpt-5.6-terra">
                  Terra — balanced coding (recommended)
                </option>
                <option value="gpt-5.6-sol">Sol — deeper reasoning</option>
                <option value="gpt-6-astra">
                  Astra — strongest, requires account access
                </option>
              </select>
            </label>
          )}
          {selectedProviders.length > 0 && (
            <>
              <label>
                <input
                  type="checkbox"
                  checked={review}
                  onChange={(e) => setReview(e.target.checked)}
                />
                Enable shell review
              </label>
              <p className="muted">
                Codex uses the smaller Luna at low thinking; Grok-only uses Grok
                4.6. Flags and errors require approval. Model access depends on
                your subscription.
              </p>
            </>
          )}
          <label>
            Additional instructions (optional)
            <textarea
              value={instructions}
              disabled={agent in status.agents}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="Add preferences or task guidance; the built-in system prompt always applies"
              rows={4}
            />
          </label>
          <details>
            <summary>Built-in system prompt (always included)</summary>
            <p className="system-prompt">{status.system_prompt}</p>
          </details>
          <p className="muted">
            Every Agent receives the built-in system prompt. Your additional
            instructions specialize it; they do not replace it.
          </p>
        </fieldset>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {busy && (
        <p role="status">
          {busy === "probe"
            ? "Checking production Sandbox… This can take up to 90 seconds per root."
            : busy === "apply"
              ? "Publishing files… Navigation is blocked until the outcome is known."
              : "Working…"}
        </p>
      )}
      {step === 3 && preview && (
        <section>
          <h2>Review files before applying</h2>
          <p className="muted">
            Destination: <code>{status.configuration_path}</code>. Existing
            resources are preserved; defaults publish last.
          </p>
          {Object.entries(preview.files).map(([name, source]) => (
            <details key={name} open>
              <summary>{name}</summary>
              <pre tabIndex={0}>{source}</pre>
            </details>
          ))}
        </section>
      )}
      <div className="actions">
        {step > 1 && (
          <button disabled={!!busy} onClick={() => setStep(step === 3 ? 2 : 1)}>
            Back
          </button>
        )}
        {step === 1 && (
          <>
            <button
              className="primary"
              disabled={!!busy || !connectionReady}
              onClick={() => setStep(2)}
            >
              Continue
            </button>
            <button
              disabled={!!busy}
              onClick={() => {
                setConnection("later");
                setProviders([]);
                setRoute("");
                setAgent("agent-default");
                setStep(2);
              }}
            >
              Not now
            </button>
          </>
        )}
        {step === 2 && (
          <button
            className="primary"
            disabled={!!busy || !path || !environmentReady}
            onClick={() => setStep(3)}
          >
            Continue
          </button>
        )}
        {step === 3 && (
          <>
            <button
              disabled={!!busy || !agent}
              onClick={() => void perform("preview")}
            >
              Preview configuration
            </button>
            <button
              className="primary"
              disabled={!!busy || !preview || !environmentReady}
              onClick={() => void perform("apply")}
            >
              Finish setup
            </button>
          </>
        )}
        {busy === "probe" && (
          <button
            onClick={() => {
              pending.current?.abort();
              setReadiness(undefined);
            }}
          >
            Cancel check
          </button>
        )}
        <button
          disabled={busy === "apply"}
          onClick={() => {
            pending.current?.abort();
            close();
          }}
        >
          Cancel setup
        </button>
      </div>
    </section>
  );
}
