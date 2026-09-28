import { useMutation } from "@tanstack/react-query";
import { Button, DisclosureSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { JsonView } from "../../shared/forms";
import layout from "./connectors.module.css";

export function ConnectorToolPreview({
  connector,
  providerId,
}: {
  connector: Schema["ConnectorApp"];
  providerId: string;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const preview = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/connector-providers/{provider_id}/apps/{app}/actions", {
          params: {
            path: {
              provider_id: providerId,
              app: connector.key,
            },
          },
        })
        .then(data),
  });
  return (
    <div>
      <Button
        variant="outline"
        size="sm"
        loading={preview.isPending}
        onClick={() => preview.mutate()}
        type="button"
      >
        {t("Preview tools")}
      </Button>
      <ErrorNotice error={preview.error} />
      {preview.data && (
        <div className={`a13n-scrollbar ${layout.preview}`}>
          <p className={layout.checkNote}>
            {t(
              "{{count}} tools. Availability is verified after authorization.",
              { count: preview.data.items.length },
            )}
          </p>
          {preview.data.items.map((tool) => (
            <DisclosureSection key={tool.name} title={tool.name}>
              <p className={layout.previewDescription}>{tool.description}</p>
              <JsonView value={tool.input_schema} />
            </DisclosureSection>
          ))}
        </div>
      )}
    </div>
  );
}
