import { useEffect, useRef, useState } from "react";
import { api, result, message, type Model } from "./client";

type Selection = Model<"SetupSelection">;
export function Setup({
  status,
  close,
  reload,
  setApplying,
}: {
  status: Model<"SetupStatus">;
  close: () => void;
  setApplying: (value: boolean) => void;
  reload: () => Promise<Model<"SetupStatus">>;
}) {
  const [providers, setProviders] = useState<Selection["providers"]>(
    status.providers.filter((p) => p.selected).map((p) => p.provider),
  );
  const [agent, setAgent] = useState(
    status.default_agent ??
      (providers?.includes("codex")
        ? "agent-codex"
        : providers?.includes("grok")
          ? "agent-grok"
          : ""),
  );
  const [project, setProject] = useState(
    status.default_project ?? "project-local",
  );
  const [path, setPath] = useState(status.suggested_project_path ?? "");
  const [environment, setEnvironment] = useState<
    Selection["environment_profile"]
  >(
    status.environment_profile === "environment-sandbox"
      ? "environment-sandbox"
      : "environment-native",
  );
  const [model, setModel] = useState<Selection["codex_model"]>("gpt-5.6-terra");
  const [review, setReview] = useState(true);
  const [preview, setPreview] = useState<Model<"SetupPreview">>();
  const [readiness, setReadiness] = useState<Model<"EnvironmentReadiness">>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const pending = useRef<AbortController | null>(null);
  const selection: Selection = {
    providers,
    default_agent: agent,
    project,
    project_path: path,
    environment_profile: environment,
    shell_review: review,
    codex_model: model,
  };
  const identity = JSON.stringify(selection);
  useEffect(() => {
    setPreview(undefined);
    setReadiness(undefined);
    pending.current?.abort();
    setBusy("");
  }, [identity]);
  useEffect(() => () => pending.current?.abort(), []);
  const agents = {
    ...Object.fromEntries(
      (providers ?? []).map((p) => [
        `agent-${p}`,
        `${p === "codex" ? "Codex" : "Grok"} coding assistant`,
      ]),
    ),
    ...status.agents,
  };
  async function perform(kind: "preview" | "probe" | "apply" | "retry") {
    if (pending.current) pending.current.abort();
    const controller = new AbortController();
    pending.current = controller;
    setBusy(kind);
    setError("");
    if (kind === "apply") setApplying(true);
    try {
      if (kind === "retry") {
        const refreshed = await reload();
        const available = refreshed.providers
          .filter((provider) => provider.selected)
          .map((provider) => provider.provider);
        setProviders(available);
        if (!agent)
          setAgent(
            available.includes("codex")
              ? "agent-codex"
              : available.includes("grok")
                ? "agent-grok"
                : "",
          );
      } else if (kind === "preview")
        setPreview(
          result(
            await api.POST("/api/setup/preview", {
              body: selection,
              signal: controller.signal,
            }),
          ),
        );
      else if (kind === "probe") {
        const candidate =
          preview ??
          result(
            await api.POST("/api/setup/preview", {
              body: selection,
              signal: controller.signal,
            }),
          );
        if (controller.signal.aborted) return;
        setPreview(candidate);
        for (const root of candidate.project_paths) {
          const checked = result(
            await api.POST("/api/environments/preflight", {
              body: { profile_id: environment, project_path: root },
              signal: controller.signal,
            }),
          );
          if (controller.signal.aborted) return;
          setReadiness(checked);
          if (!checked.ready) break;
        }
      } else if (preview) {
        const publication = result(
          await api.POST("/api/setup/apply", {
            body: { selection, expected_generation: preview.generation },
            signal: controller.signal,
          }),
        );
        if (!publication.completed) {
          setPreview(undefined);
          setError(
            `${publication.error_message ?? "Publication incomplete"} Published files: ${publication.published_paths.join(", ") || "none"}. Review the retained files and preview again.`,
          );
          return;
        }
        await reload();
        setApplying(false);
        close();
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        if (kind === "apply") {
          setPreview(undefined);
          setError(
            `${message(error)} Publication may be partial. Review files and preview again before applying.`,
          );
        } else setError(message(error));
      }
    } finally {
      if (kind === "apply") setApplying(false);
      if (pending.current === controller) {
        setBusy("");
        pending.current = null;
      }
    }
  }
  return (
    <section className="setup" aria-labelledby="setup-title">
      <div className="eyebrow">FIRST-USE SETUP</div>
      <h1 id="setup-title">Your agent, your machine.</h1>
      <p className="muted">
        Connect an existing subscription, review editable defaults, then start a
        conversation. No login, model request, or file write happens until you
        explicitly choose the corresponding action.
      </p>
      {status.diagnostic && (
        <p role="alert" className="error">
          {status.diagnostic}
        </p>
      )}
      <fieldset disabled={!!busy}>
        <legend>1. Compatible subscriptions</legend>
        {status.providers.map((provider) => (
          <div className="provider" key={provider.provider}>
            <label>
              <input
                type="checkbox"
                checked={providers?.includes(provider.provider) ?? false}
                disabled={!provider.available}
                onChange={(event) => {
                  const next = event.target.checked
                    ? [...(providers ?? []), provider.provider]
                    : (providers ?? []).filter((p) => p !== provider.provider);
                  setProviders(next);
                  if (!status.agents[agent])
                    setAgent(
                      next.includes("codex")
                        ? "agent-codex"
                        : next.includes("grok")
                          ? "agent-grok"
                          : "",
                    );
                }}
              />
              {provider.provider === "codex" ? "Codex" : "Grok"}
              <span className="badge">
                {provider.available ? "Available" : "Not signed in"}
              </span>
            </label>
            {!provider.available && (
              <p className="muted">
                In another terminal, run{" "}
                <code>a13n-ui auth login {provider.provider}</code>, finish
                login, then Retry account discovery. {provider.diagnostic}
              </p>
            )}
          </div>
        ))}
        <button onClick={() => void perform("retry")}>
          Retry account discovery
        </button>
        <label>
          Codex model
          <select
            value={model}
            onChange={(event) =>
              setModel(event.target.value as Selection["codex_model"])
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
        <p className="muted">
          Grok uses grok-4.6. Model access depends on your subscription. These
          are editable starting points, not an entitlement guarantee.
        </p>
        <label>
          <input
            type="checkbox"
            checked={review}
            onChange={(e) => setReview(e.target.checked)}
          />
          Enable shell review
        </label>
        <p className="muted">
          With Codex, shell review uses the smaller GPT-5.6 Luna at low
          thinking. Grok-only uses Grok-4.6; no cheaper current compatible model
          is assumed. Review failures request approval instead of silently
          allowing commands.
        </p>
      </fieldset>
      <fieldset disabled={!!busy}>
        <legend>2. Defaults for new conversations</legend>
        <label>
          Default agent
          <select value={agent} onChange={(e) => setAgent(e.target.value)}>
            <option value="" disabled>
              Select an agent
            </option>
            {Object.entries(agents).map(([id, name]) => (
              <option value={id} key={id}>
                {name} ({id})
              </option>
            ))}
          </select>
        </label>
        <label>
          Project
          <select value={project} onChange={(e) => setProject(e.target.value)}>
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
          <input value={path} onChange={(e) => setPath(e.target.value)} />
        </label>
        <p className="muted">
          Existing project files and their roots are preserved; this path
          applies only to a new local project.
        </p>
      </fieldset>
      <fieldset disabled={!!busy}>
        <legend>3. Execution authority</legend>
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
          Runs as your host account, with ambient filesystem and network access.
          Shell review is not a sandbox.
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
          Checks the exact production agent-envd runtime and filesystem,
          process, and denied-network isolation. System security policy is never
          changed for you.
        </p>
        {environment === "environment-sandbox" && (
          <button disabled={!agent} onClick={() => void perform("probe")}>
            Check Sandbox / Retry
          </button>
        )}
      </fieldset>
      {readiness && (
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
            <button onClick={() => setEnvironment("environment-native")}>
              Choose Full Control (no Sandbox)
            </button>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {busy && (
        <p role="status">
          {busy === "probe"
            ? "Checking production Sandbox… This can take up to 90 seconds."
            : busy === "apply"
              ? "Publishing files… Navigation is temporarily blocked until the outcome is known."
              : "Working…"}
        </p>
      )}
      {preview && (
        <section>
          <h2>Review files before applying</h2>
          <p className="muted">
            Destination: <code>{status.configuration_path}</code>. Existing
            resources are preserved. Defaults publish last; a partial failure
            retains completed files and reports recovery instructions.
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
        <button
          disabled={!!busy || !agent || !path}
          onClick={() => void perform("preview")}
        >
          Preview configuration
        </button>
        <button
          className="primary"
          disabled={
            !!busy ||
            !preview ||
            (environment === "environment-sandbox" && !readiness?.ready)
          }
          onClick={() => void perform("apply")}
        >
          Apply and continue
        </button>
        <button
          disabled={busy === "apply"}
          onClick={() => {
            pending.current?.abort();
            close();
          }}
        >
          Cancel
        </button>
      </div>
      <p className="muted">
        Cancelling setup does not create files. Applied resources remain
        editable YAML; changing defaults does not modify existing conversations.
      </p>
    </section>
  );
}
