import { Button, ChoiceField, ModalFrame, Spinner } from "a13n-ui";
import { FileUpload } from "../../shared/file-upload";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Download, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { downloadBlob } from "../../shared/download";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  Timestamp,
} from "../../shared/feedback";
import { Confirm, FormActions, JsonView } from "../../shared/form";
import styles from "../../shared/shared.module.css";

export function AssetsPage() {
  const { workspace, can } = useWorkspace(),
    client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    page = useCursor();
  const [source, setSource] = useState<Schema["AssetSourceKind"] | "">(""),
    [open, setOpen] = useState(false),
    [upload, setUpload] = useState<{ file: File; key: string }>();
  const query = useQuery({
    queryKey: ["assets", workspace.id, source, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/assets", {
          params: {
            path: { workspace: workspace.id },
            query: { cursor: page.cursor, source_kind: source || undefined },
          },
          signal,
        })
        .then(data),
  });
  const mutation = useMutation({
    mutationFn: async () => {
      if (!upload) throw new Error(t("Choose a file first."));
      return client.http
        .POST("/api/v1/workspaces/{workspace}/assets", {
          params: {
            path: { workspace: workspace.id },
            query: {
              filename: upload.file.name,
              media_type: upload.file.type || "application/octet-stream",
            },
            header: commandHeaders(workspace.id, upload.key),
          },
          headers: { "Content-Type": "application/octet-stream" },
          body: upload.file,
        })
        .then(data);
    },
    onSuccess: () => {
      setOpen(false);
      setUpload(undefined);
      void cache.invalidateQueries({ queryKey: ["assets"] });
    },
  });
  const download = useMutation({
    mutationFn: async (asset: Schema["Asset"]) => {
      const result = await client.http.GET(
        "/api/v1/assets/{asset_id}/content",
        { params: { path: { asset_id: asset.id } }, parseAs: "blob" },
      );
      downloadBlob(data(result), asset.filename);
    },
  });
  return (
    <Page
      title={t("Assets")}
      description={t(
        "Files uploaded by your team and published by agent runs.",
      )}
      actions={
        can("asset.create") && (
          <ModalFrame
            onOpenChange={(value) => {
              if (!mutation.isPending) {
                setOpen(value);
                mutation.reset();
              }
            }}
            trigger={
              <Button variant="default" type="button">
                {<Upload size={15} />}
                {t("Upload asset")}
              </Button>
            }
            size={"md"}
            title={t("Upload asset")}
            description={t(
              "Each upload creates an immutable file. Upload a new asset to replace content.",
            )}
            closeLabel={t("Close")}
            open={open}
          >
            <form
              className={styles.form}
              onSubmit={(event) => {
                event.preventDefault();
                mutation.mutate();
              }}
            >
              <FileUpload
                label={t("File")}
                file={upload?.file}
                onSelect={(file) => {
                  setUpload(
                    file ? { file, key: crypto.randomUUID() } : undefined,
                  );
                }}
              />
              <ErrorNotice error={mutation.error} />
              <FormActions pending={mutation.isPending} label={t("Upload")} />
            </form>
          </ModalFrame>
        )
      }
    >
      <div className={styles.toolbar}>
        <ChoiceField
          placeholder={t("All sources")}
          value={source || "all"}
          onValueChange={(value) => {
            setSource(
              value === "upload" || value === "run_output" ? value : "",
            );
            page.reset();
          }}
          label={t("Source")}
          hideLabel
          options={[
            { value: "all", label: t("All sources") },
            { value: "upload", label: t("Uploaded") },
            { value: "run_output", label: t("Run output") },
          ]}
        />
      </div>
      <ErrorNotice error={query.error ?? download.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            columns={[
              {
                label: t("File"),
                render: (item) => (
                  <>
                    <strong>{item.filename}</strong>
                    <small>{item.media_type}</small>
                  </>
                ),
              },
              {
                label: t("Size"),
                render: (item) =>
                  `${new Intl.NumberFormat().format(item.size_bytes)} B`,
              },
              {
                label: t("Source"),
                render: (item) =>
                  t(item.source.kind === "upload" ? "Uploaded" : "Run output"),
              },
              {
                label: t("Created"),
                render: (item) => <Timestamp value={item.created_at} />,
              },
              {
                label: t("Actions"),
                align: "right",
                render: (item) => (
                  <div className={styles.actions}>
                    <Button
                      size="sm"
                      variant="outline"
                      loading={
                        download.isPending && download.variables?.id === item.id
                      }
                      onClick={() => download.mutate(item)}
                      type="button"
                    >
                      {download.isPending &&
                      download.variables?.id === item.id ? (
                        <Spinner />
                      ) : (
                        <Download size={14} />
                      )}
                      {t("Download")}
                    </Button>
                    <ModalFrame
                      trigger={
                        <Button size="sm" variant="outline" type="button">
                          {t("Details")}
                        </Button>
                      }
                      size={"md"}
                      title={item.filename}
                      description={t("Immutable file metadata and provenance.")}
                      closeLabel={t("Close")}
                    >
                      <JsonView value={item} />
                    </ModalFrame>
                    {can("asset.delete") && (
                      <Confirm
                        title={t("Delete asset")}
                        description={t(
                          "Future reads and new runs cannot use this file. This cannot be undone.",
                        )}
                        trigger={t("Delete")}
                        danger
                        action={() =>
                          client.http.DELETE("/api/v1/assets/{asset_id}", {
                            params: { path: { asset_id: item.id } },
                          })
                        }
                      />
                    )}
                  </div>
                ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No assets yet")}
            description={t("Upload a file or publish one from an agent run.")}
          />
        )
      )}
    </Page>
  );
}
