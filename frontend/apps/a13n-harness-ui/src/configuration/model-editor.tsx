import { useEffect, useRef, useState } from "react";
import {
  Key,
  SlidersHorizontal,
  ArrowCounterClockwise,
} from "@phosphor-icons/react";
import management from "./management.module.css";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, SearchPicker } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import { ProviderAccount } from "../setup/accounts";
import { useSubscriptions } from "../setup/subscriptions";
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

export function connectionId(
  recipe: Recipe | null,
  connections: readonly Connection[],
) {
  if (!recipe) return connections[0]?.id ?? "";
  return (
    connections.find(
      (item) =>
        item.authentication === recipe.authentication.kind &&
        item.provider === recipe.route.split(":")[0],
    )?.id ?? recipe.route.split(":")[0]
  );
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
  const [expanded, setExpanded] = useState(
    !value || value.kind !== "api_key" || !(value.env || value.credential_ref),
  );
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
      setExpanded(false);
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
      {!expanded ? (
        <div className={management.credentialSummary}>
          <span className={management.inlineLabel}>
            <Key aria-hidden="true" />
            {selected === "env" && value?.kind === "api_key"
              ? `Environment · ${value.env}`
              : keys.data?.some((key) => key.credential_ref === selected)
                ? "Stored API key"
                : "API key reference"}
          </span>
          <Button variant="ghost" onClick={() => setExpanded(true)}>
            Change credential
          </Button>
        </div>
      ) : (
        <>
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
              description="Read from the server environment."
            />
          ) : (
            !selected && (
              <>
                <FormField
                  label="Provider API key"
                  description="Shared server credential. Saved separately from the model."
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
        </>
      )}
      <ErrorNotice error={save.error || keys.error} />
    </fieldset>
  );
}

/** Shared draft editor. Preparing a connection never publishes configuration. */
type ModelEditorProps = {
  value: Recipe | null;
  onChange: (recipe: Recipe, suggestedName?: string) => void;
  onReady?: (ready: boolean) => void;
  onRecipeReady?: (ready: boolean) => void;
  draft?: ModelEditorDraft;
  onDraftChange?: (draft: ModelEditorDraft) => void;
};

export function ModelEditor(props: ModelEditorProps) {
  const { client } = useTransport();
  const choices = useQuery({
    queryKey: ["model-choices"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/models/choices", { signal })),
  });
  if (!choices.data)
    return (
      <>
        <ErrorNotice error={choices.error} />
        <p role="status">Loading model connections…</p>
      </>
    );
  return <LoadedModelEditor {...props} choices={choices.data} />;
}

function LoadedModelEditor({
  choices,
  value,
  onChange,
  onReady,
  onRecipeReady,
  draft,
  onDraftChange,
}: ModelEditorProps & { choices: Schema<"ModelChoices"> }) {
  const { client } = useTransport();
  const [connection, setConnection] = useState(
    () => draft?.connection ?? connectionId(value, choices.connections ?? []),
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
  const [modelSearch, setModelSearch] = useState("");
  const [customContext, setCustomContext] = useState(false);
  const [customAffinity, setCustomAffinity] = useState(false);
  const [accountReady, setAccountReady] = useState(false);
  const [accountIdentity, setAccountIdentity] = useState("");
  const savedRoute = value?.route;
  const savedConnection = connectionId(value, choices.connections ?? []);
  const previousConnection = useRef(savedConnection);
  const previousRoute = useRef(savedRoute);
  useEffect(() => {
    if (
      (previousRoute.current !== savedRoute ||
        previousConnection.current !== savedConnection) &&
      value
    ) {
      setConnection(savedConnection);
      setModelId(value.route.slice(value.route.indexOf(":") + 1));
    }
    previousRoute.current = savedRoute;
    previousConnection.current = savedConnection;
  }, [savedRoute, savedConnection]);
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
  const selected = choices.connections?.find((item) => item.id === connection);
  const effectiveId =
    modelId || (!value ? (selected?.default_model ?? "") : "");
  const effectiveUrl = baseUrl || (!value ? (selected?.base_url ?? "") : "");
  const api = selected?.authentication === "api_key";
  const nativeProvider =
    selected?.account?.provider === "codex" ||
    selected?.account?.provider === "grok"
      ? selected.account.provider
      : undefined;
  const subscriptions = useSubscriptions(!!nativeProvider);
  const selectedGroup =
    authentication?.kind === "codex_subscription" ||
    authentication?.kind === "grok_subscription"
      ? authentication.group
      : undefined;
  const groupId = selectedGroup ?? `${nativeProvider}-default`;
  const nativeGroup = subscriptions.data?.groups.find(
    (item) => item.id === groupId && item.provider === nativeProvider,
  );
  const groupReady = nativeGroup?.accounts?.some((member) =>
    subscriptions.data?.accounts.some(
      (item) =>
        item.account.id === member.account &&
        item.account.enabled &&
        item.authentication.availability === "available",
    ),
  );
  const catalog = useQuery({
    queryKey: ["model-directory"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/models/catalog", { signal })),
    enabled: api,
  });
  const options = useQuery({
    queryKey: [
      "model-options",
      connection,
      effectiveId,
      effectiveUrl,
      value?.settings,
    ],
    queryFn: ({ signal }) =>
      result(
        client.POST("/api/models/options", {
          body: {
            connection,
            model_id: effectiveId,
            base_url: effectiveUrl,
            settings: value?.settings,
          },
          signal,
        }),
      ),
    enabled: !!selected && !!effectiveId,
  });
  const presets = options.data?.presets ?? [];
  const presetKeys = [
    ...new Set(presets.flatMap((preset) => Object.keys(preset.settings))),
  ];
  const selectedPreset = presets.find((preset) =>
    presetKeys.every(
      (key) =>
        JSON.stringify(value?.settings?.[key]) ===
        JSON.stringify(preset.settings[key]),
    ),
  );
  const contextBudget = value?.model_characteristics?.context_window_tokens;
  const contextChoices = options.data?.context_choices ?? [];
  const affinityHeader = String(
    value?.model_configuration?.session_affinity_header ?? "",
  );
  const matches =
    !!value &&
    connectionId(value, choices.connections ?? []) === connection &&
    value.route ===
      `${selected?.provider ?? value.route.split(":")[0]}:${effectiveId}` &&
    String(value.model_configuration?.base_url ?? "") === effectiveUrl;
  const authReady = api
    ? authentication?.kind === "api_key" &&
      !!(authentication.env || authentication.credential_ref)
    : nativeProvider
      ? nativeGroup
        ? !!groupReady
        : !selectedGroup &&
          !subscriptions.data?.adoption[nativeProvider]?.group_created &&
          accountReady
      : accountReady;
  // Publishing a recipe is independent of account login. Never publish the
  // previous recipe while the editor displays unapplied connection choices.
  const recipeReady = matches && (api ? !!authReady : true);
  useEffect(() => {
    onRecipeReady?.(recipeReady);
  }, [recipeReady, onRecipeReady]);
  useEffect(() => {
    onReady?.(recipeReady && !!authReady);
  }, [recipeReady, authReady, onReady]);
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
    mutationFn: async (reset: boolean) => ({
      revision,
      recipe: await result(
        client.POST("/api/models/prepare", {
          body: {
            connection,
            model_id: effectiveId,
            base_url: effectiveUrl,
            ...(!reset && value
              ? {
                  settings: value.settings ?? {},
                  model_characteristics: value.model_characteristics ?? {},
                }
              : {}),
            ...(authentication?.kind === selected?.authentication
              ? { authentication }
              : {}),
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
      onChange(
        {
          ...prepared.recipe,
          model_configuration: api
            ? {
                ...value?.model_configuration,
                ...prepared.recipe.model_configuration,
                base_url: prepared.recipe.model_configuration?.base_url,
              }
            : prepared.recipe.model_configuration,
        },
        options.data?.name,
      );
    },
  });
  useEffect(() => {
    if (matches || !selected || !effectiveId || (api && !authReady)) return;
    const timer = setTimeout(() => prepare.mutate(false), 200);
    return () => clearTimeout(timer);
  }, [revision, matches, authReady]);
  const discoveryEpoch = useRef(0);
  useEffect(() => {
    discoveryEpoch.current += 1;
  }, [connection, accountReady, accountIdentity]);
  const discovery = useMutation({
    mutationFn: async () => {
      const epoch = discoveryEpoch.current;
      const provider = selected!.account!.provider;
      const models = await result(
        client.POST("/api/auth/accounts/{provider}/models", {
          params: { path: { provider } },
        }),
      );
      return { epoch, models, identity: accountIdentity, connection };
    },
  });
  const currentDiscovery =
    accountReady &&
    discovery.data?.epoch === discoveryEpoch.current &&
    discovery.data.identity === accountIdentity &&
    discovery.data.connection === connection
      ? discovery.data.models
      : undefined;
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
  if (currentDiscovery)
    for (const item of currentDiscovery)
      candidates.set(item.value, {
        value: item.value,
        label: item.label,
        description: "Account catalog · Chat Completions",
      });
  if (effectiveId && !candidates.has(effectiveId))
    candidates.set(effectiveId, {
      value: effectiveId,
      label: effectiveId,
      description: "Custom model ID",
    });
  if (modelSearch.trim() && !candidates.has(modelSearch.trim()))
    candidates.set(modelSearch.trim(), {
      value: modelSearch.trim(),
      label: modelSearch.trim(),
      description: "Use custom model ID",
    });
  return (
    <div className={styles.stack}>
      <ErrorNotice error={options.error || prepare.error} />
      <ChoiceField
        label="Model connection"
        value={connection}
        options={
          choices.connections?.map((item) => ({
            value: item.id,
            label: item.label,
          })) ?? []
        }
        onValueChange={(id) => {
          if (id === connection) return;
          const next = choices.connections!.find((item) => item.id === id)!;
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
        ) : nativeProvider && selected.account ? (
          <>
            <ErrorNotice error={subscriptions.error} />
            <ChoiceField
              label="Account group"
              value={selectedGroup ?? ""}
              onValueChange={(group) =>
                setAuth({
                  kind:
                    nativeProvider === "codex"
                      ? "codex_subscription"
                      : "grok_subscription",
                  ...(group ? { group } : {}),
                })
              }
              options={[
                {
                  value: "",
                  label: "Provider default group",
                  hint:
                    !nativeGroup &&
                    subscriptions.data?.adoption[nativeProvider]?.group_created
                      ? "Default group is missing. Select or create a group."
                      : undefined,
                },
                ...(subscriptions.data?.groups
                  .filter((item) => item.provider === nativeProvider)
                  .map((item) => ({
                    value: item.id,
                    label: item.name,
                    hint: `${item.accounts?.length ?? 0} accounts · ${item.strategy === "weighted" ? "Weighted balance" : "Ordered consumption"}`,
                  })) ?? []),
              ]}
            />
            {(!nativeGroup &&
              !subscriptions.data?.adoption[nativeProvider]?.group_created) ||
            nativeGroup?.accounts?.some(
              (member) => member.account === `acct-${nativeProvider}-default`,
            ) ? (
              <ProviderAccount
                key={connection}
                connection={selected.account}
                inline
                onReady={setAccountReady}
                onIdentity={setAccountIdentity}
              />
            ) : (
              <p>
                {groupReady
                  ? "Group has a connected account."
                  : "This group needs a connected member account."}
              </p>
            )}
            <Link
              to={`/settings/accounts?tab=groups${nativeGroup ? `&group=${encodeURIComponent(nativeGroup.id)}` : ""}`}
            >
              Manage accounts and groups
            </Link>
          </>
        ) : selected.account ? (
          <ProviderAccount
            key={connection}
            connection={selected.account}
            inline
            onReady={setAccountReady}
            onIdentity={setAccountIdentity}
          />
        ) : null)}
      {selected?.account?.model_discovery && (
        <>
          <Button
            disabled={!accountReady}
            loading={discovery.isPending}
            onClick={() => discovery.mutate()}
          >
            Fetch account models
          </Button>
          <small>
            Explicitly queries this account's catalog. Only Chat Completions
            models are shown; access is still subject to your plan and
            organization policy.
          </small>
          <ErrorNotice error={discovery.error} />
          {currentDiscovery?.length === 0 && (
            <p role="status">
              No compatible models returned. You can still enter a model ID
              manually.
            </p>
          )}
        </>
      )}
      {!selected && (
        <p>This connection is managed in Advanced configuration.</p>
      )}
      <FormField label="Model">
        <SearchPicker
          label="Model"
          placeholder="Find a model"
          emptyMessage="Search or enter a custom model ID."
          onSearchChange={setModelSearch}
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
      </FormField>

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
      {!matches && prepare.isPending && (
        <small role="status">Preparing model…</small>
      )}
      {!matches && prepare.isError && (
        <Button variant="outline" onClick={() => prepare.mutate(false)}>
          Retry
        </Button>
      )}
      {selected && matches && value && (
        <>
          <details className={styles.details}>
            <summary>
              <span className={management.inlineLabel}>
                <SlidersHorizontal aria-hidden="true" />
                Model defaults
              </span>
            </summary>
            <div className={styles.stack}>
              <small>
                {[
                  selectedPreset?.label ?? "Custom settings",
                  contextBudget
                    ? `${contextBudget.toLocaleString()} context`
                    : "Native context",
                  value.settings?.max_tokens
                    ? `${Number(value.settings.max_tokens).toLocaleString()} output`
                    : null,
                  value.settings?.openai_service_tier === "priority"
                    ? "Priority speed"
                    : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </small>
              {!api && !authReady && (
                <small>Connect an account before running this model.</small>
              )}
              <ChoiceField
                label="Settings preset"
                value={selectedPreset?.value ?? ""}
                options={[
                  ...(!selectedPreset
                    ? [{ value: "", label: "Custom settings" }]
                    : []),
                  ...presets.map((item, index) => ({
                    value: item.value,
                    label: `${item.label}${index === 0 ? " (default)" : ""}`,
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
              {(options.data?.reasoning_mode?.supported ||
                value.settings?.openai_reasoning_mode != null) && (
                <ChoiceField
                  label="Reasoning mode"
                  value={String(value.settings?.openai_reasoning_mode ?? "")}
                  description={
                    options.data?.reasoning_mode?.reason ??
                    "Saved Model default, independent of thinking effort. Pro access, usage and latency depend on the provider."
                  }
                  options={[
                    { value: "", label: "Provider default" },
                    {
                      value: "standard",
                      label: "Standard",
                      disabled: !options.data?.reasoning_mode?.supported,
                    },
                    {
                      value: "pro",
                      label: "Pro",
                      disabled: !options.data?.reasoning_mode?.supported,
                    },
                    ...(![undefined, null, "standard", "pro"].includes(
                      value.settings?.openai_reasoning_mode as
                        string | null | undefined,
                    )
                      ? [
                          {
                            value: String(
                              value.settings?.openai_reasoning_mode,
                            ),
                            label: `Current: ${value.settings?.openai_reasoning_mode}`,
                            disabled: true,
                          },
                        ]
                      : []),
                  ]}
                  onValueChange={(mode) => {
                    const settings = { ...value.settings };
                    if (mode) settings.openai_reasoning_mode = mode;
                    else delete settings.openai_reasoning_mode;
                    update({ settings });
                  }}
                />
              )}
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
                      settings: {
                        ...value.settings,
                        openai_service_tier: tier,
                      },
                    })
                  }
                />
              )}
              {
                <ChoiceField
                  label="Context budget"
                  value={
                    customContext ||
                    (contextBudget != null &&
                      !contextChoices.some(
                        (choice) => choice.value === contextBudget,
                      ))
                      ? "custom"
                      : contextBudget == null
                        ? ""
                        : String(contextBudget)
                  }
                  options={[
                    { value: "", label: "Native default" },
                    { value: "custom", label: "Custom budget" },
                    ...contextChoices.map((choice) => ({
                      value: String(choice.value),
                      label: `${choice.label} · ${choice.value.toLocaleString()} tokens`,
                    })),
                  ]}
                  onValueChange={(context) => {
                    setCustomContext(context === "custom");
                    if (context === "custom") return;
                    update({
                      model_characteristics: {
                        ...value.model_characteristics,
                        context_window_tokens: context ? Number(context) : null,
                      },
                    });
                  }}
                />
              }
              {(customContext ||
                (contextBudget != null &&
                  !contextChoices.some(
                    (choice) => choice.value === contextBudget,
                  ))) && (
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
              )}
            </div>
          </details>
          <details className={styles.details}>
            <summary>
              <span className={management.inlineLabel}>
                <SlidersHorizontal aria-hidden="true" />
                Advanced connection options
              </span>
            </summary>
            <div className={styles.stack}>
              {selected?.supports_session_affinity && (
                <ChoiceField
                  label="Gateway session affinity"
                  value={
                    customAffinity ||
                    (affinityHeader &&
                      !choices.session_affinity_presets?.some(
                        (preset) => preset.header === affinityHeader,
                      ))
                      ? "custom"
                      : affinityHeader
                  }
                  options={[
                    { value: "", label: "None" },
                    { value: "custom", label: "Custom header" },
                    ...(choices.session_affinity_presets ?? []).map(
                      (preset) => ({
                        value: preset.header,
                        label: preset.label,
                      }),
                    ),
                  ]}
                  onValueChange={(header) => {
                    setCustomAffinity(header === "custom");
                    if (header === "custom") return;
                    const config = { ...value.model_configuration };
                    if (header) config.session_affinity_header = header;
                    else delete config.session_affinity_header;
                    update({ model_configuration: config });
                  }}
                />
              )}
              {selected?.supports_session_affinity &&
                (customAffinity ||
                  (affinityHeader &&
                    !choices.session_affinity_presets?.some(
                      (preset) => preset.header === affinityHeader,
                    ))) && (
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
              <Button
                variant="outline"
                loading={prepare.isPending}
                onClick={() => prepare.mutate(true)}
              >
                <ArrowCounterClockwise aria-hidden="true" />
                Restore recommended defaults
              </Button>
            </div>
          </details>
        </>
      )}
    </div>
  );
}

export function ModelFields({
  source,
  onChange,
  onReady,
}: {
  source: string;
  onChange: (source: string) => void;
  onReady?: (ready: boolean) => void;
}) {
  const raw = readDocument(source)?.toJS();
  const characteristics = raw?.model_characteristics
    ? { ...raw.model_characteristics }
    : raw?.model_characteristics;
  const contextAlias =
    characteristics &&
    "context_window" in characteristics &&
    (!("context_window_tokens" in characteristics) ||
      characteristics.context_window_tokens === characteristics.context_window);
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
      value={value}
      onRecipeReady={onReady}
      onChange={(recipe, suggestedName) => {
        let next = source;
        if (!value && raw?.name === "Untitled" && suggestedName)
          next = updateDocument(next, ["name"], suggestedName);
        const fields = { ...recipe };
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
