import { useState } from "react";
import { Link } from "react-router";
import { Button, ModalFrame } from "a13n-ui";
import { Plus } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { useSources, useWebProviders } from "../transport/context";
import { ErrorNotice, PageHeader, SearchField } from "../shell/ui";
import { DraftLinks, NewResourceButton } from "./sources";
import { WebProviderIdentity, WebProviderPicker } from "./web-providers";
import layout from "./settings.module.css";
import styles from "../shell/workbench.module.css";

export function WebProvidersSection() {
  const { t } = useTranslation();
  const catalog = useWebProviders();
  const sources = useSources();
  const [adding, setAdding] = useState(false);
  const [type, setType] = useState("");
  const [search, setSearch] = useState("");
  const definition = catalog.data?.types.find((item) => item.type === type);
  const accounts = catalog.data?.accounts ?? [];
  const visible = accounts.filter((item) =>
    `${item.name} ${item.type} ${item.id}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <section className={layout.collection}>
      <PageHeader
        level={2}
        title={t("Web providers")}
        description={t(
          "Reusable search and scrape accounts. Select them in each agent's Web options.",
        )}
        actions={
          <Button variant="outline" onClick={() => setAdding(true)}>
            <Plus />
            {t("Add Web provider")}
          </Button>
        }
      />
      {accounts.length > 0 && (
        <SearchField
          label={t("Search Web accounts")}
          value={search}
          onChange={setSearch}
        />
      )}
      <ErrorNotice
        error={catalog.error || sources.error}
        retry={() => void catalog.refetch()}
      />
      <DraftLinks kinds={["web_provider"]} search={search} />
      <div className={styles.resourceList}>
        {visible.map((account) => {
          const path = sources.data?.sources.find(
            (item) =>
              item.resource_kind === "web_provider" &&
              item.resource_ids.includes(account.id),
          )?.relative_path;
          const definition = catalog.data?.types.find(
            (item) => item.type === account.type,
          );
          return (
            path && (
              <Link
                key={account.id}
                className={styles.resourceRow}
                to={`/settings/source?path=${encodeURIComponent(path)}`}
              >
                <div>
                  <strong>
                    <WebProviderIdentity
                      type={account.type}
                      name={account.name}
                    />
                  </strong>
                  <small>
                    {definition?.display_name ?? account.type} ·{" "}
                    {definition?.operations
                      .map((operation) =>
                        t(operation === "search" ? "Search" : "Scrape"),
                      )
                      .join(" · ")}
                  </small>
                </div>
                <span className={styles.tag}>{t("Saved")}</span>
              </Link>
            )
          );
        })}
      </div>
      {!catalog.isPending && !accounts.length && !catalog.error && (
        <p className={layout.emptySection}>
          {t(
            "No Web accounts yet. Local search and HTML scraping remain available without one.",
          )}
        </p>
      )}
      {!!accounts.length && !visible.length && (
        <p className={layout.emptySection}>{t("No matching Web accounts.")}</p>
      )}
      <ModalFrame
        open={adding}
        onOpenChange={setAdding}
        title={t("Add Web provider")}
        closeLabel={t("Close")}
        footer={
          <>
            <Button variant="outline" onClick={() => setAdding(false)}>
              {t("Cancel")}
            </Button>
            {definition && (
              <NewResourceButton
                kind="web_provider"
                label={t("Continue")}
                initial={{
                  type: definition.type,
                  name: definition.display_name,
                }}
              />
            )}
          </>
        }
      >
        <WebProviderPicker value={type} onChange={setType} />
      </ModalFrame>
    </section>
  );
}
