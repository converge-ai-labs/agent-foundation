import { KeyIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { ChoiceField } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  Toolbar,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { Page, PageActions } from "../../shared/page";
import { credentialQueries } from "./api";
import { useCredentialEditor } from "./editor";
import styles from "./credentials.module.css";

export function CredentialsPage() {
  const client = useClient(),
    { workspace, basePath, can } = useWorkspace(),
    { t } = useTranslation(),
    navigate = useNavigate();
  const [search, setSearch] = useState(""),
    [state, setState] = useState("all");
  const term = search.trim().toLocaleLowerCase(),
    filtered = !!term || state !== "all";
  const page = useCursor({ term, state });
  const query = useQuery({
    ...credentialQueries(client, workspace.id).list(page.cursor, term, state),
    enabled: can("read"),
  });
  const rows = query.data?.items ?? [];
  const editor = useCredentialEditor({
    operation: { kind: "create" },
    onSaved: (saved) => void navigate(`${basePath}/credentials/${saved.id}`),
  });
  const create = can("write") && editor.button;
  return (
    <Page
      title={t("Credentials")}
      description={t(
        "Manage tokens for authenticated HTTPS requests from agent environments.",
      )}
      toolbar={
        <Toolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel={t("Search credentials")}
          filters={
            <ChoiceField
              label={t("Status")}
              variant="filter"
              value={state}
              onValueChange={setState}
              options={[
                { value: "all", label: t("All statuses") },
                { value: "active", label: t("state.active") },
                { value: "revoked", label: t("state.revoked") },
              ]}
            />
          }
        />
      }
    >
      <PageActions>{create}</PageActions>
      {query.isPending ? (
        <Loading variant="table" columns={5} />
      ) : query.error ? (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      ) : rows.length ? (
        <>
          <ResourceTable
            items={rows}
            caption={t("Credentials")}
            onRowActivate={(row) =>
              navigate(`${basePath}/credentials/${row.id}`)
            }
            columns={[
              {
                label: t("Credential"),
                tone: "primary",
                render: (row) => (
                  <ResourceIdentity
                    name={row.name}
                    description={row.description}
                    resourceId={row.id}
                    icon={<KeyIcon size={16} aria-hidden="true" />}
                    to={`${basePath}/credentials/${row.id}`}
                  />
                ),
              },
              {
                label: t("Allowed domains"),
                render: (row) => (
                  <span className={styles.domains}>
                    {row.allowed_hosts.join(", ")}
                  </span>
                ),
              },
              {
                label: t("Status"),
                render: (row) => <StatePill state={row.state} />,
              },
              {
                label: t("Credential version"),
                render: (row) => row.credential_version,
              },
              {
                label: t("Updated"),
                tone: "muted",
                render: (row) => <Timestamp value={row.updated_at} relative />,
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} credentials on this page", {
              count: rows.length,
            })}
          >
            <Pagination page={page} next={query.data?.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        <Empty
          icon={<KeyIcon aria-hidden="true" />}
          title={t(filtered ? "No matching credentials" : "No credentials yet")}
          description={t(
            filtered
              ? "Change or clear the search and filters."
              : "Add a token and allow the domains your agents need.",
          )}
          action={!filtered && create}
        />
      )}
      {editor.dialog}
    </Page>
  );
}
