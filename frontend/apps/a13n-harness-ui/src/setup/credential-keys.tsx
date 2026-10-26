import { useState } from "react";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Link } from "react-router";
import { parseDocument } from "yaml";
import { Button, Input, ModalFrame } from "a13n-ui";
import { KeyIcon, PlusIcon } from "@phosphor-icons/react";
import { useSources, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import styles from "./subscriptions.module.css";

export function CredentialKeys({
  connections,
}: {
  connections: Schema<"ModelConnection">[];
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const sources = useSources();
  const credentialSources = (sources.data?.sources ?? []).filter(
    (source) =>
      source.resource_kind === "model" || source.resource_kind === "device",
  );
  const resources = useQueries({
    queries: credentialSources.map((source) => ({
      queryKey: ["source", source.relative_path],
      enabled: source.content_available,
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        result(
          client.GET("/api/configuration/sources/{relative_path}", {
            params: { path: { relative_path: source.relative_path } },
            signal,
          }),
        ),
    })),
  });
  const associations = resources.flatMap((resource, index) => {
    if (!resource.data?.content) return [];
    const kind = credentialSources[index].resource_kind;
    const document = parseDocument(resource.data.content);
    const reference = document.getIn(["authentication", "credential_ref"]);
    const route = document.get("route");
    const name = document.get("name");
    if (typeof reference !== "string") return [];
    const provider =
      kind === "model" && typeof route === "string"
        ? route.split(":")[0]
        : null;
    return [
      {
        reference,
        provider,
        kind,
        name: typeof name === "string" ? name : resource.data.relative_path,
        path: resource.data.relative_path,
      },
    ];
  });
  const unresolved =
    sources.isPending ||
    !!sources.error ||
    credentialSources.some(
      (source, i) =>
        !source.content_available ||
        resources[i].isPending ||
        !!resources[i].error,
    );
  const [editing, setEditing] = useState<"new" | { reference: string } | null>(
    null,
  );
  const [reference, setReference] = useState("");
  const [secret, setSecret] = useState("");
  const [filter, setFilter] = useState("");
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
      setEditing(null);
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
  const grouped = new Map<
    string,
    { reference: string; resources: typeof associations }[]
  >();
  for (const key of keys.data ?? []) {
    const linked = associations.filter(
      (item) => item.reference === key.credential_ref,
    );
    const providers = [
      ...new Set(
        linked.flatMap((item) => (item.provider ? [item.provider] : [])),
      ),
    ];
    const provider =
      providers.length > 1
        ? "Shared across providers"
        : providers.length === 1
          ? (connections.find(
              (connection) =>
                connection.provider === providers[0] &&
                connection.authentication === "api_key",
            )?.label ?? providers[0])
          : linked.some((item) => item.kind === "device")
            ? "Devices"
            : unresolved
              ? "Usage not resolved"
              : "Not linked to a Model or Device";
    if (
      !`${key.credential_ref} ${provider} ${linked.map((item) => item.name).join(" ")}`
        .toLowerCase()
        .includes(filter.toLowerCase())
    )
      continue;
    grouped.set(provider, [
      ...(grouped.get(provider) ?? []),
      { reference: key.credential_ref, resources: linked },
    ]);
  }
  const duplicate =
    editing === "new" &&
    keys.data?.some((key) => key.credential_ref === reference.trim());
  return (
    <section className={styles.section} aria-label="Saved API keys">
      <header className={styles.heading}>
        <div>
          <h2>Saved API keys</h2>
          <p>
            Keys for Models and Devices, grouped by Model provider or Device
            use. Key values stay on the server.
          </p>
        </div>
        <Button
          onClick={() => {
            setReference("");
            setSecret("");
            save.reset();
            setEditing("new");
          }}
        >
          <PlusIcon /> Add API key
        </Button>
      </header>
      <ErrorNotice
        error={
          keys.error ||
          sources.error ||
          resources.find((resource) => resource.error)?.error
        }
      />
      <Input
        aria-label="Filter API keys"
        placeholder="Find a key, Model, or Device…"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        className={styles.search}
      />
      {saved && <p role="status">{saved}</p>}
      {keys.isPending && <p role="status">Loading keys…</p>}
      {[...grouped]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([provider, items]) => (
          <section
            className={styles.providerSection}
            key={provider}
            aria-label={`${provider} keys`}
          >
            <header className={styles.providerHeading}>
              <KeyIcon size={20} />
              <h3>{provider}</h3>
              <span>
                {items.length} {items.length === 1 ? "key" : "keys"}
              </span>
            </header>
            <div className={styles.rows}>
              {items.map((key) => (
                <article className={styles.row} key={key.reference}>
                  <div className={styles.identity}>
                    <strong>{key.reference}</strong>
                    <span>
                      {key.resources.length
                        ? "Used by"
                        : unresolved
                          ? "Model and Device links are not fully available."
                          : "Select this saved key in a Model’s or Device’s authentication settings."}
                    </span>
                    <div className={styles.modelLinks}>
                      {key.resources.map((resource) => (
                        <Link
                          key={resource.path}
                          to={`/settings/source?path=${encodeURIComponent(resource.path)}`}
                        >
                          {resource.kind === "device" ? "Device" : "Model"}:{" "}
                          {resource.name}
                        </Link>
                      ))}
                    </div>
                  </div>
                  <div className={styles.actions}>
                    <Button
                      variant="outline"
                      onClick={() => {
                        setReference(key.reference);
                        setSecret("");
                        save.reset();
                        setEditing({ reference: key.reference });
                      }}
                    >
                      Replace key
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => {
                        remove.reset();
                        setDeleting(key.reference);
                      }}
                    >
                      Remove
                    </Button>
                  </div>
                </article>
              ))}
            </div>
          </section>
        ))}
      {!keys.isPending && !keys.error && !grouped.size && (
        <p className={styles.empty}>
          {filter
            ? "No keys match your search."
            : "No saved keys yet. Add an API key, then select it in a Model or Device."}
        </p>
      )}
      <p className={styles.help}>
        These credentials are shared by Models and Devices, separate from your
        browser’s instance access key.
      </p>
      <ModalFrame
        open={!!editing}
        onOpenChange={(open) => {
          if (!open && !save.isPending) {
            setEditing(null);
            setSecret("");
          }
        }}
        title={editing === "new" ? "Add API key" : "Replace API key"}
        description={
          editing === "new"
            ? "Save a reusable key, then link it from a Model or Device."
            : "Linked Models and Devices will use the replacement when they next load this credential."
        }
        closeLabel="Cancel"
        footer={
          <Button
            type="submit"
            form="save-credential-key"
            loading={save.isPending}
            disabled={!secret.trim() || !reference.trim() || !!duplicate}
          >
            Save key
          </Button>
        }
      >
        <form
          id="save-credential-key"
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            if (!duplicate)
              save.mutate({ credential_ref: reference.trim(), key: secret });
          }}
        >
          <ErrorNotice error={save.error} />
          {editing === "new" ? (
            <TextField
              label="Saved key name"
              value={reference}
              onChange={setReference}
            />
          ) : (
            <strong>{reference}</strong>
          )}
          {duplicate && (
            <p role="alert">
              This name is already saved. Use Replace key to update it.
            </p>
          )}
          <TextField
            label="API key"
            type="password"
            value={secret}
            onChange={setSecret}
          />
        </form>
      </ModalFrame>
      <ModalFrame
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setDeleting(null);
        }}
        title="Remove saved key?"
        description="Models and Devices referencing this credential will need a replacement key to authenticate."
        closeLabel="Cancel"
        footer={
          <Button
            variant="destructive"
            loading={remove.isPending}
            onClick={() => deleting && remove.mutate(deleting)}
          >
            Remove key
          </Button>
        }
      >
        <p>{deleting}</p>
        <ul>
          {associations
            .filter((item) => item.reference === deleting)
            .map((item) => (
              <li key={item.path}>
                {item.kind === "device" ? "Device" : "Model"}: {item.name}
              </li>
            ))}
        </ul>
        {unresolved && <p>Some Model and Device links are unavailable.</p>}
        <ErrorNotice error={remove.error} />
      </ModalFrame>
    </section>
  );
}
