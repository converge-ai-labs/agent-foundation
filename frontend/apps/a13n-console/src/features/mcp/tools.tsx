import { CaretDownIcon, WrenchIcon } from "@phosphor-icons/react";
import {
  Button,
  Collapsible,
  CollapsibleTrigger,
  CollapsiblePanel,
  Input,
} from "a13n-ui";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { Empty } from "../../shared/collection";
import { ErrorNotice } from "../../shared/feedback";
import { JsonView } from "../../shared/forms";
import styles from "./mcp.module.css";

export function MCPTools({ connection }: { connection: Schema["Connection"] }) {
  const { t } = useTranslation(),
    client = useClient(),
    [search, setSearch] = useState(""),
    [limit, setLimit] = useState(30);
  const discovery = useMutation({
    mutationFn: () =>
      client.http
        .POST("/api/v1/connections/{connection_id}/mcp/discover", {
          params: { path: { connection_id: connection.id } },
          body: { expected_version: connection.version },
        })
        .then(data),
  });
  const tools =
    discovery.data?.items.filter((tool) =>
      `${tool.name} ${tool.description}`
        .toLocaleLowerCase()
        .includes(search.toLocaleLowerCase()),
    ) ?? [];
  return (
    <div className={styles.tools}>
      <div className={styles.toolsHeader}>
        <p>{t("Browse the tools available from this server.")}</p>
        <Button
          size="sm"
          variant="outline"
          loading={discovery.isPending}
          onClick={() => discovery.mutate()}
          type="button"
        >
          {t("Discover tools")}
        </Button>
      </div>
      <ErrorNotice error={discovery.error} />
      {discovery.data && (
        <>
          <Input
            type="search"
            size="sm"
            aria-label={t("Search tools")}
            placeholder={t("Search tools")}
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setLimit(30);
            }}
          />
          <div className={styles.toolList}>
            {tools.slice(0, limit).map((tool) => (
              <Collapsible key={tool.name}>
                <CollapsibleTrigger className={styles.toolRow}>
                  <span className={styles.toolCopy}>
                    <strong>{tool.name}</strong>
                    {tool.description && <small>{tool.description}</small>}
                  </span>
                  <CaretDownIcon aria-hidden size={14} />
                </CollapsibleTrigger>
                <CollapsiblePanel>
                  <div className={styles.toolDetail}>
                    <ToolSchema
                      title={t("Input schema")}
                      value={tool.input_schema}
                    />
                    {tool.output_schema && (
                      <ToolSchema
                        title={t("Output schema")}
                        value={tool.output_schema}
                      />
                    )}
                    {tool.annotations &&
                      Object.keys(tool.annotations).length > 0 && (
                        <ToolSchema
                          title={t("Annotations")}
                          value={tool.annotations}
                        />
                      )}
                  </div>
                </CollapsiblePanel>
              </Collapsible>
            ))}
          </div>
          {tools.length > limit && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setLimit((value) => value + 30)}
              type="button"
            >
              {t("Show more tools")}
            </Button>
          )}
          {!tools.length && (
            <Empty
              icon={<WrenchIcon aria-hidden="true" />}
              title={t("No tools found")}
              description={t(
                "Try another search or check the server's authorization.",
              )}
            />
          )}
        </>
      )}
    </div>
  );
}

function ToolSchema({ title, value }: { title: string; value: unknown }) {
  return (
    <section className={styles.toolSchema}>
      <h4>{title}</h4>
      <JsonView value={value} />
    </section>
  );
}
