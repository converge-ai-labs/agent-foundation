import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, SearchPicker } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import { ProviderAccount } from "../setup/accounts";
import { readDocument, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";

type Recipe = Schema<"ModelRecipe">;
type Connection = Schema<"ModelConnection">;
export type ModelEditorDraft = {
  connection: string;
  modelId: string;
  baseUrl: string;
  authentication?: Recipe["authentication"];
};

export function connectionId(recipe: Recipe | null) {
  if (recipe?.authentication.kind === "codex_subscription") return "codex";
  if (recipe?.authentication.kind === "grok_subscription")
    return "grok-subscription";
  return recipe?.route.split(":")[0] ?? "codex";
}

/** Secrets never leave this memory-only component except for an explicit key save. */
function CredentialField({
  connection,
  value,
  onChange,
}: {
  connection: Connection;
  value: Recipe["authentication"] | undefined;
  onChange: (value: Recipe["authentication"]) => void;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const [secret, setSecret] = useState("");
  // Allocate once: a lost acknowledgement is reconciled against this same reference.
  const [reference, setReference] = useState(
    () => `key-${crypto.randomUUID().replaceAll("-", "")}`,
  );
  const keys = useQuery({
    queryKey: ["keys"],
    queryFn: ({ signal }) => result(client.GET("/api/auth/keys", { signal })),
  });
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const latestAuth = useRef(JSON.stringify(value));
  latestAuth.current = JSON.stringify(value);
  const save = useMutation({
    mutationFn: async () => {
      const attempt = { reference, authentication: JSON.stringify(value) };
      await result(
        client.PUT("/api/auth/keys", {
          body: { credential_ref: reference, key: secret },
        }),
      );
      return attempt;
    },
    onSuccess: (attempt) => {
      void queries.invalidateQueries({ queryKey: ["keys"] });
      if (!active.current || attempt.authentication !== latestAuth.current)
        return;
      setSecret("");
      onChange({ kind: "api_key", credential_ref: attempt.reference });
      setReference(`key-${crypto.randomUUID().replaceAll("-", "")}`);
    },
    onError: () => {
      void queries.invalidateQueries({ queryKey: ["keys"] });
    },
  });
  const selected =
    value?.kind === "api_key"
      ? value.env != null
        ? "env"
        : (value.credential_ref ?? "")
      : "";
  const choices = [
    { value: "", label: "Save a new API key" },
    ...(keys.data ?? []).map((key) => ({
      value: key.credential_ref,
      label: key.credential_ref,
    })),
    { value: "env", label: "Server environment variable" },
  ];
  if (selected && !choices.some((item) => item.value === selected))
    choices.push({ value: selected, label: `${selected} (saved reference)` });
  return (
    <fieldset disabled={save.isPending} className={styles.stack}>
      <ChoiceField
        label="Credential source"
        value={selected}
        options={choices}
        onValueChange={(key) => {
          setSecret("");
          onChange(
            key === "env"
              ? {
                  kind: "api_key",
                  env: connection.credential_env ?? "PROVIDER_API_KEY",
                }
              : { kind: "api_key", credential_ref: key },
          );
        }}
      />
      {selected === "env" ? (
        <TextField
          label="Environment variable"
          value={value?.kind === "api_key" ? (value.env ?? "") : ""}
          onChange={(env) => onChange({ kind: "api_key", env })}
          description="Resolved on the server at runtime, never in this browser."
        />
      ) : (
        !selected && (
          <>
            <FormField
              label="Provider API key"
              description="Saved independently on this server. Cancelling the editor does not delete a saved key."
            >
              <Input
                type="password"
                autoComplete="new-password"
                value={secret}
                onChange={(event) => setSecret(event.target.value)}
              />
            </FormField>
            <Button
              variant="outline"
              loading={save.isPending}
              disabled={!secret.trim()}
              onClick={() => save.mutate()}
            >
              Save API key
            </Button>
            {keys.data?.some((key) => key.credential_ref === reference) && (
              <Button
                variant="outline"
                onClick={() => {
                  setSecret("");
                  onChange({ kind: "api_key", credential_ref: reference });
                  setReference(
                    `key-${crypto.randomUUID().replaceAll("-", "")}`,
                  );
                }}
              >
                Use saved key from this attempt
              </Button>
            )}
          </>
        )
      )}
      <ErrorNotice error={save.error || keys.error} />
      <small>
        Credentials are shared by this server. A saved reference is not proof of
        model access.
      </small>
    </fieldset>
  );
}

/** Shared authoring surface. Discovery never changes a saved recipe; defaults require an explicit action. */
export function ModelEditor({
  value,
  onChange,
  onReady,
  draft,
  onDraftChange,
}: {
  value: Recipe | null;
  onChange: (recipe: Recipe, suggestedName?: string) => void;
  onReady?: (ready: boolean) => void;
  draft?: ModelEditorDraft;
  onDraftChange?: (draft: ModelEditorDraft) => void;
}) {
  const { client } = useTransport();
  const choices = useQuery({
    queryKey: ["model-choices"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/models/choices", { signal })),
  });
  const [connection, setConnection] = useState(
    () => draft?.connection ?? connectionId(value),
  );
  const [modelId, setModelId] = useState(
    () =>
      draft?.modelId ?? value?.route.slice(value.route.indexOf(":") + 1) ?? "",
  );
  const [baseUrl, setBaseUrl] = useState(
    () => draft?.baseUrl ?? String(value?.model_configuration?.base_url ?? ""),
  );
  const [authentication, setAuthentication] = useState(
    draft?.authentication ?? value?.authentication,
  );
  const [accountReady, setAccountReady] = useState(false);
  const savedAuthentication = JSON.stringify(value?.authentication);
  const previousAuth = useRef(savedAuthentication);
  useEffect(() => {
    if (previousAuth.current !== savedAuthentication)
      setAuthentication(value?.authentication);
    previousAuth.current = savedAuthentication;
  }, [savedAuthentication]);
  const savedBaseUrl = String(value?.model_configuration?.base_url ?? "");
  const previousUrl = useRef(savedBaseUrl);
  useEffect(() => {
    if (previousUrl.current !== savedBaseUrl) setBaseUrl(savedBaseUrl);
    previousUrl.current = savedBaseUrl;
  }, [savedBaseUrl]);
  useEffect(() => {
    onDraftChange?.({ connection, modelId, baseUrl, authentication });
  }, [connection, modelId, baseUrl, authentication, onDraftChange]);
  const selected = choices.data?.connections?.find(
    (item) => item.id === connection,
  );
  const effectiveId =
    modelId || (!value ? (selected?.default_model ?? "") : "");
  const effectiveUrl = baseUrl || (!value ? (selected?.base_url ?? "") : "");
  const api = selected?.authentication === "api_key";
  const catalog = useQuery({
    queryKey: ["model-directory"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/models/catalog", { signal })),
    enabled: api,
  });
  const options = useQuery({
    queryKey: ["model-options", connection, effectiveId, effectiveUrl],
    queryFn: ({ signal }) =>
      result(
        client.POST("/api/models/options", {
          body: { connection, model_id: effectiveId, base_url: effectiveUrl },
          signal,
        }),
      ),
    enabled: !!selected && !!effectiveId,
  });
  const matches =
    !!value &&
    connectionId(value) === connection &&
    value.route === `${selected?.provider}:${effectiveId}` &&
    String(value.model_configuration?.base_url ?? "") === effectiveUrl;
  const authReady = api
    ? authentication?.kind === "api_key" &&
      !!(authentication.env || authentication.credential_ref)
    : accountReady;
  useEffect(() => {
    onReady?.(!!matches && !!authReady);
  }, [matches, authReady, onReady]);
  // An in-flight prepare may not replace later input or advanced source edits.
  const revision = JSON.stringify([
    connection,
    effectiveId,
    effectiveUrl,
    authentication,
    value,
  ]);
  const latest = useRef(revision);
  latest.current = revision;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const prepare = useMutation({
    mutationFn: async () => ({
      revision,
      recipe: await result(
        client.POST("/api/models/prepare", {
          body: {
            connection,
            model_id: effectiveId,
            base_url: effectiveUrl,
            ...(api ? { authentication } : {}),
          },
        }),
      ),
    }),
    onSuccess: (prepared) => {
      if (!mounted.current || prepared.revision !== latest.current) return;
      setModelId(
        prepared.recipe.route.slice(prepared.recipe.route.indexOf(":") + 1),
      );
      setBaseUrl(String(prepared.recipe.model_configuration?.base_url ?? ""));
      onChange(prepared.recipe, options.data?.name);
    },
  });
  const update = (change: Partial<Recipe>) => {
    if (value) onChange({ ...value, ...change });
  };
  const setAuth = (auth: Recipe["authentication"]) => {
    setAuthentication(auth);
    if (matches) update({ authentication: auth });
  };
  const directory = (catalog.data?.items ?? []).filter(
    (item) => item.connection === connection,
  );
  const candidates = new Map(
    (selected?.models ?? []).map((item) => [
      item.value,
      {
        value: item.value,
        label: item.label,
        description: "Release recommendation",
      },
    ]),
  );
  for (const item of directory)
    if (!candidates.has(item.model_id))
      candidates.set(item.model_id, {
        value: item.model_id,
        label: item.name,
        description: [
          item.model_id,
          item.context_window
            ? `${item.context_window.toLocaleString()} context`
            : "",
          item.released ?? "",
        ]
          .filter(Boolean)
          .join(" · "),
      });
  if (effectiveId && !candidates.has(effectiveId))
    candidates.set(effectiveId, {
      value: effectiveId,
      label: effectiveId,
      description: "Custom model ID",
    });
  return (
    <div className={styles.stack}>
      <ErrorNotice error={choices.error || options.error || prepare.error} />
      <ChoiceField
        label="Model connection"
        value={connection}
        options={
          choices.data?.connections?.map((item) => ({
            value: item.id,
            label: item.label,
          })) ?? []
        }
        onValueChange={(id) => {
          const next = choices.data!.connections!.find(
            (item) => item.id === id,
          )!;
          setConnection(id);
          setModelId(next.default_model);
          setBaseUrl(next.base_url ?? "");
          setAuthentication(undefined);
          setAccountReady(false);
        }}
      />
      {selected &&
        (api ? (
          <CredentialField
            key={connection}
            connection={selected}
            value={authentication}
            onChange={setAuth}
          />
        ) : (
          <ProviderAccount
            key={connection}
            provider={connection === "codex" ? "codex" : "grok"}
            inline
            onReady={setAccountReady}
          />
        ))}
      <SearchPicker
        label="Model"
        placeholder="Find a model"
        emptyMessage="Enter a custom model ID below."
        value={effectiveId}
        onValueChange={setModelId}
        groups={[
          {
            label: api
              ? "Recommendations & public directory"
              : "Subscription models",
            options: [...candidates.values()],
          },
        ]}
      />
      <TextField
        label="Model ID"
        value={effectiveId}
        onChange={setModelId}
        description="Enter a custom ID if needed. Listed models do not guarantee account access."
      />
      {api && (catalog.isPending || catalog.error) && (
        <small role="status">
          {catalog.isPending
            ? "Loading model directory…"
            : "Directory unavailable. Enter a model ID or choose a bundled recommendation."}
        </small>
      )}
      {selected?.supports_base_url && (
        <TextField
          label="Base URL"
          value={effectiveUrl}
          onChange={setBaseUrl}
          description="Optional API endpoint override. No credentials, query string or fragment."
        />
      )}
      {(!matches || !value) && (
        <>
          {value && (
            <p>
              Applying this connection resets model-specific settings to
              reviewed defaults.
            </p>
          )}
          <Button
            loading={prepare.isPending}
            disabled={!options.data || !!options.error || (api && !authReady)}
            onClick={() => prepare.mutate()}
          >
            {value ? "Apply connection & defaults" : "Use this model"}
          </Button>
        </>
      )}
      {matches && value && (
        <>
          <p role="status">{value.route} · Access not tested</p>
          <ChoiceField
            label="Settings preset"
            value=""
            options={[
              { value: "", label: "Keep current settings" },
              ...(options.data?.presets ?? []).map((item) => ({
                value: item.value,
                label: item.label,
              })),
            ]}
            onValueChange={(preset) => {
              const settings = options.data?.presets.find(
                (item) => item.value === preset,
              )?.settings;
              if (settings) {
                const next = { ...value.settings };
                for (const choice of options.data?.presets ?? [])
                  for (const key of Object.keys(choice.settings))
                    delete next[key];
                update({ settings: { ...next, ...settings } });
              }
            }}
          />
          {options.data?.supports_service_tier && (
            <ChoiceField
              label="Service speed"
              value={String(value.settings?.openai_service_tier ?? "auto")}
              options={[
                { value: "auto", label: "Standard" },
                { value: "priority", label: "Fast · priority usage" },
                ...(![undefined, "auto", "priority"].includes(
                  value.settings?.openai_service_tier as string | undefined,
                )
                  ? [
                      {
                        value: String(value.settings?.openai_service_tier),
                        label: `Current: ${value.settings?.openai_service_tier}`,
                      },
                    ]
                  : []),
              ]}
              onValueChange={(tier) =>
                update({
                  settings: { ...value.settings, openai_service_tier: tier },
                })
              }
            />
          )}
          {!!options.data?.context_choices?.length && (
            <ChoiceField
              label="Context preset"
              value=""
              options={[
                { value: "", label: "Keep current budget" },
                ...options.data.context_choices.map((choice) => ({
                  value: String(choice.value),
                  label: `${choice.label} · ${choice.value.toLocaleString()} tokens`,
                })),
              ]}
              onValueChange={(context) => {
                if (context)
                  update({
                    model_characteristics: {
                      ...value.model_characteristics,
                      context_window_tokens: Number(context),
                    },
                  });
              }}
            />
          )}
          <TextField
            label="Working context budget"
            type="number"
            value={String(
              value.model_characteristics?.context_window_tokens ?? "",
            )}
            onChange={(input) =>
              update({
                model_characteristics: {
                  ...value.model_characteristics,
                  context_window_tokens: input ? Number(input) : null,
                },
              })
            }
            description={
              options.data?.known_context_window
                ? `Bundled limit: ${options.data.known_context_window.toLocaleString()} tokens.`
                : "Leave blank for the native default."
            }
          />
          <details className={styles.details}>
            <summary>Advanced connection options</summary>
            {selected?.supports_session_affinity && (
              <ChoiceField
                label="Gateway session affinity"
                value=""
                options={[
                  { value: "", label: "Keep current header" },
                  ...(choices.data?.session_affinity_presets ?? []).map(
                    (preset) => ({ value: preset.header, label: preset.label }),
                  ),
                ]}
                onValueChange={(header) => {
                  if (header)
                    update({
                      model_configuration: {
                        ...value.model_configuration,
                        session_affinity_header: header,
                      },
                    });
                }}
              />
            )}
            {selected?.supports_session_affinity && (
              <TextField
                label="Session affinity header"
                value={String(
                  value.model_configuration?.session_affinity_header ?? "",
                )}
                onChange={(header) => {
                  const config = { ...value.model_configuration };
                  if (header) config.session_affinity_header = header;
                  else delete config.session_affinity_header;
                  update({ model_configuration: config });
                }}
                description="Optional header for compatible gateways. Subscriptions use native affinity."
              />
            )}
            <p>
              Native settings, model configuration and characteristics remain in
              the configuration file. Explicit values and unknown model IDs are
              preserved.
            </p>
          </details>
          <div>
            <strong>Native tool support</strong>
            <p>
              {options.data?.native_tools
                ?.map((tool) => tool.label)
                .join(", ") || "No guided native tools for this connection."}
            </p>
            <small>Enable native tools in the Agent editor.</small>
          </div>
        </>
      )}
    </div>
  );
}

export function ModelFields({
  source,
  onChange,
}: {
  source: string;
  onChange: (source: string) => void;
}) {
  const raw = readDocument(source)?.toJS();
  const characteristics = raw?.model_characteristics
    ? { ...raw.model_characteristics }
    : raw?.model_characteristics;
  const contextAlias =
    characteristics &&
    "context_window" in characteristics &&
    !("context_window_tokens" in characteristics);
  if (contextAlias) {
    characteristics.context_window_tokens = characteristics.context_window;
    delete characteristics.context_window;
  }
  const value: Recipe | null =
    typeof raw?.route === "string" && raw.route && raw?.authentication
      ? {
          route: raw.route,
          authentication: raw.authentication,
          settings: raw.settings,
          model_configuration: raw.model_configuration,
          model_characteristics: characteristics,
        }
      : null;
  return (
    <ModelEditor
      key={`${connectionId(value)}:${value?.route ?? "new"}`}
      value={value}
      onChange={(recipe, suggestedName) => {
        let next = source;
        if (!value && raw?.name === "Untitled" && suggestedName)
          next = updateDocument(next, ["name"], suggestedName);
        const fields = { ...recipe };
        if (contextAlias && fields.model_characteristics) {
          const policy: Record<string, unknown> = {
            ...fields.model_characteristics,
          };
          policy.context_window = policy.context_window_tokens;
          delete policy.context_window_tokens;
          fields.model_characteristics = policy;
        }
        const patch = (path: string[], previous: unknown, field: unknown) => {
          if (JSON.stringify(previous) === JSON.stringify(field)) return;
          if (
            previous &&
            field &&
            typeof previous === "object" &&
            typeof field === "object" &&
            !Array.isArray(previous) &&
            !Array.isArray(field)
          ) {
            const before = previous as Record<string, unknown>;
            const after = field as Record<string, unknown>;
            for (const key of new Set([
              ...Object.keys(before),
              ...Object.keys(after),
            ]))
              patch([...path, key], before[key], after[key]);
          } else next = updateDocument(next, path, field);
        };
        for (const [key, field] of Object.entries(fields))
          patch([key], raw?.[key], field);
        onChange(next);
      }}
    />
  );
}
