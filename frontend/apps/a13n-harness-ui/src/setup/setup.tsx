import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Textarea } from "a13n-ui";
import { useSetup, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, PageHeader, Panel, TextField } from "../shell/ui";
import { SourceEditor } from "../configuration/editor";
import styles from "../shell/workbench.module.css";

export function Preflight({
  profile,
  projectPath,
}: {
  profile: "environment-native" | "environment-sandbox";
  projectPath?: string;
}) {
  const { client } = useTransport();
  const preflight = useMutation({
    mutationFn: () =>
      result(
        client.POST("/api/environments/preflight", {
          body: { profile_id: profile, project_path: projectPath || "." },
        }),
      ),
  });
  useEffect(() => {
    preflight.reset();
  }, [profile, projectPath]);
  return (
    <div className={styles.stack}>
      <Button
        variant="outline"
        loading={preflight.isPending}
        onClick={() => preflight.mutate()}
      >
        Check environment readiness
      </Button>
      <ErrorNotice error={preflight.error} />
      {preflight.data && (
        <div role="status" className={styles.notice}>
          <strong>
            {preflight.data.ready ? "Environment ready" : "Action required"}
          </strong>
          <p>{preflight.data.message}</p>
          {preflight.data.instructions?.map((instruction) => (
            <p key={instruction}>{instruction}</p>
          ))}
        </div>
      )}
    </div>
  );
}

export function SetupPage() {
  const setup = useSetup();
  const { client } = useTransport();
  const queries = useQueryClient();
  const [connection, setConnection] = useState("codex");
  const [route, setRoute] = useState("");
  const [credential, setCredential] = useState("key-primary");
  const [environment, setEnvironment] = useState<
    "environment-native" | "environment-sandbox"
  >("environment-native");
  const [project, setProject] = useState("");
  const [projectPath, setProjectPath] = useState("");
  const [instructions, setInstructions] = useState("");
  const [shellReview, setShellReview] = useState(true);
  const [includeChildren, setIncludeChildren] = useState(true);
  const [previewed, setPreviewed] = useState<Schema<"SetupSelection"> | null>(
    null,
  );
  const selection: Schema<"SetupSelection"> = {
    providers:
      connection === "codex" || connection === "grok" ? [connection] : [],
    ...(connection === "api_key"
      ? {
          api_key_model: {
            route,
            authentication: { kind: "api_key", credential_ref: credential },
          },
        }
      : {}),
    default_agent:
      setup.data?.needed === false
        ? (setup.data.default_agent ?? "agent-default")
        : connection === "api_key"
          ? "agent-api-key"
          : `agent-${connection}`,
    environment_profile: environment,
    instructions,
    shell_review: shellReview,
    ...(setup.data?.needed
      ? { include_default_subagents: includeChildren }
      : {}),
    ...(project ? { project, project_path: projectPath } : {}),
  };
  const selectionKey = JSON.stringify(selection);
  const preview = useMutation({
    mutationFn: (body: Schema<"SetupSelection">) =>
      result(client.POST("/api/setup/preview", { body })),
    onSuccess: (_, body) => setPreviewed(body),
  });
  const apply = useMutation({
    mutationFn: (body: Schema<"SetupSelection">) => {
      setPreviewed(null);
      return result(
        client.POST("/api/setup/apply", { body: { selection: body } }),
      );
    },
    onSuccess: () => {
      void queries.invalidateQueries();
    },
  });
  const previewCurrent =
    previewed && JSON.stringify(previewed) === selectionKey;
  const available = setup.data?.providers.find(
    (provider) => provider.provider === connection,
  );
  return (
    <>
      <PageHeader
        title={
          setup.data?.needed === false
            ? "Setup & readiness"
            : "Set up your workbench"
        }
        description="Connect a model, choose the execution environment, and review what will be written. No model calls are made here."
      />
      <ErrorNotice error={setup.error || preview.error || apply.error} />
      {apply.error && (
        <div className={styles.notice}>
          Publication may have changed files even though its response was lost
          or rejected. Inspect Resources and preview again before publishing
          another time. Your selections are retained.
        </div>
      )}
      {setup.isPending && <p role="status">Discovering configuration…</p>}
      {setup.data?.diagnostic && (
        <div className={styles.notice}>{setup.data.diagnostic}</div>
      )}
      <div className={styles.twoColumns}>
        <Panel title="1. Model connection">
          <div className={styles.stack}>
            <ChoiceField
              label="Connection"
              value={connection}
              options={[
                { value: "codex", label: "Codex account" },
                { value: "grok", label: "Grok account" },
                { value: "api_key", label: "Model API key" },
              ]}
              onValueChange={setConnection}
            />
            {connection === "api_key" ? (
              <>
                <TextField
                  label="Model route"
                  value={route}
                  onChange={setRoute}
                  description="Registered provider route, for example openai-responses:<model>."
                />
                <TextField
                  label="Saved credential reference"
                  value={credential}
                  onChange={setCredential}
                />
              </>
            ) : (
              <p>
                {available?.available
                  ? "Account available on this server."
                  : available?.diagnostic ||
                    "Connect this provider account before running an agent."}
              </p>
            )}
            <Link
              to="/settings/accounts"
              target="_blank"
              rel="noopener noreferrer"
            >
              Manage provider accounts and keys (opens in a new tab)
            </Link>
            <p>
              Guided setup uses the server's model defaults. Edit generated
              Model resources afterward for a specific route or provider
              settings.
            </p>
          </div>
        </Panel>
        <Panel title="2. Execution environment">
          <div className={styles.stack}>
            <ChoiceField
              label="Environment"
              value={environment}
              options={[
                {
                  value: "environment-native",
                  label: "Full Control · server host",
                },
                { value: "environment-sandbox", label: "Sandbox" },
              ]}
              onValueChange={(value) =>
                setEnvironment(value as typeof environment)
              }
            />
            <p>
              {environment === "environment-native"
                ? "The agent can execute on the server account. Shell review provides approval prompts, not isolation."
                : "The sandbox provider must be configured and reachable from the server."}
            </p>
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={shellReview}
                onChange={(event) => setShellReview(event.target.checked)}
              />
              Review shell commands
            </label>
            {environment === "environment-native" ? (
              <Preflight
                profile={environment}
                projectPath={projectPath || undefined}
              />
            ) : (
              <p>
                Preview files first, then check each resolved host path below.
                Sandbox publication requires successful server preflight for
                those paths.
              </p>
            )}
          </div>
        </Panel>
      </div>
      <Panel title="3. Workspace & instructions">
        <div className={styles.stack}>
          <div className={styles.formGrid}>
            <TextField
              label="Project ID (optional)"
              value={project}
              onChange={setProject}
              description="Leave blank to work without a Project."
            />
            <TextField
              label="Existing host directory"
              value={projectPath}
              onChange={setProjectPath}
              disabled={!project}
              description={setup.data?.suggested_project_path}
            />
          </div>
          <FormField
            label="Additional agent instructions"
            description="Optional additions. The release system prompt is supplied separately by the server."
          >
            <Textarea
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              rows={7}
            />
          </FormField>
          {setup.data?.needed && (
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={includeChildren}
                onChange={(event) => setIncludeChildren(event.target.checked)}
              />
              Include built-in subagents
            </label>
          )}
        </div>
      </Panel>
      <Panel title="4. Review & publish">
        <p>
          Review complete generated files. Existing authored resources are
          preserved unless this setup explicitly updates them.
        </p>
        <div className={styles.actions}>
          <Button
            variant="outline"
            loading={preview.isPending}
            disabled={
              !setup.data ||
              apply.isPending ||
              (!!project && !projectPath) ||
              (connection === "api_key" && (!route || !credential))
            }
            onClick={() => preview.mutate(selection)}
          >
            Preview files
          </Button>
          <Button
            loading={apply.isPending}
            disabled={!previewCurrent}
            onClick={() => apply.mutate(previewed!)}
          >
            Publish setup
          </Button>
        </div>
        {preview.data && (
          <>
            {!previewCurrent && (
              <p>Settings changed. Preview again before publishing.</p>
            )}
            {previewCurrent &&
              environment === "environment-sandbox" &&
              preview.data.project_paths.map((path) => (
                <div key={path} className={styles.stack}>
                  <code>{path}</code>
                  <Preflight profile={environment} projectPath={path} />
                </div>
              ))}
            {Object.entries(preview.data.files).map(([path, content]) => (
              <details key={path} className={styles.details}>
                <summary>{path}</summary>
                <SourceEditor
                  value={content}
                  readOnly
                  label={`Preview ${path}`}
                />
              </details>
            ))}
            <p>
              Preserved:{" "}
              {preview.data.preserved_paths.join(", ") || "No existing paths"}
            </p>
          </>
        )}
        {apply.data && (
          <div role="status" className={styles.notice}>
            <strong>
              {apply.data.completed
                ? "Setup files published"
                : "Setup was not completed"}
            </strong>
            <p>
              {apply.data.error_message ??
                "Configuration has been rediscovered. Check the accepted generation and account/environment readiness before starting a run."}
            </p>
            <p>{apply.data.published_paths.join(", ")}</p>
            <Link to="/">Open workbench</Link>
          </div>
        )}
      </Panel>
    </>
  );
}
