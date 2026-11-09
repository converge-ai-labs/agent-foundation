import { useTranslation } from "react-i18next";
import { SettingsToggle } from "../shell/settings";
import { useQuery } from "@tanstack/react-query";
import { useSources, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { connectionId } from "./model-editor";
import { readDocument } from "./documents";

type Tool = Schema<"ModelToolChoice">;
export function nativeToolKind(value: unknown): string | undefined {
  if (!value || typeof value !== "object") return;
  const item = value as {
    capability?: string;
    configuration?: { kind?: string };
  };
  return item.capability === "native_image_generation"
    ? "image_generation"
    : item.capability === "NativeTool"
      ? item.configuration?.kind
      : undefined;
}

export function toggleNativeTool(source: string, tool: Tool, enabled: boolean) {
  const document = readDocument(source)!;
  const capabilities: {
    capability: string;
    configuration?: Record<string, unknown>;
  }[] = document.toJS().capabilities ?? [];
  const index = capabilities.findIndex(
    (item) => nativeToolKind(item) === tool.value,
  );
  if (index >= 0 && !enabled) document.deleteIn(["capabilities", index]);
  if (index < 0 && enabled && tool.capability) {
    if (!document.has("capabilities"))
      document.set("capabilities", document.createNode([]));
    document.addIn(["capabilities"], document.createNode(tool.capability));
  }
  // Keep Host fetch/download and unrelated web settings. Only the corresponding
  // search/scrape operation changes when the user explicitly toggles its native tool.
  if (tool.replaces_host_operation) {
    const updated = document.toJS().capabilities as typeof capabilities;
    let webIndex = updated.findIndex((item) => item.capability === "web");
    if (webIndex < 0) {
      webIndex = updated.length;
      document.addIn(
        ["capabilities"],
        document.createNode({
          capability: "web",
          configuration: {},
        }),
      );
    }
    document.setIn(
      [
        "capabilities",
        webIndex,
        "configuration",
        tool.replaces_host_operation,
        "mode",
      ],
      enabled ? "off" : "host",
    );
  }
  return document.toString();
}

export function AgentNativeTools({
  source,
  onChange,
}: {
  source: string;
  onChange: (source: string) => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const sources = useSources();
  const raw = readDocument(source)?.toJS();
  const model = sources.data?.sources.find(
    (item) =>
      item.resource_kind === "model" && item.resource_ids.includes(raw?.model),
  );
  const saved = useQuery({
    queryKey: ["source", model?.relative_path],
    enabled: !!model?.content_available,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: model!.relative_path } },
          signal,
        }),
      ),
  });
  const recipe = readDocument(saved.data?.content ?? "")?.toJS() as
    Schema<"ModelRecipe"> | undefined;
  const choices = useQuery({
    queryKey: ["model-choices"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/models/choices", { signal })),
  });
  const request =
    recipe?.route && recipe.authentication && choices.data
      ? {
          connection: connectionId(recipe, choices.data.connections ?? []),
          model_id: recipe.route.slice(recipe.route.indexOf(":") + 1),
          base_url:
            typeof recipe.model_configuration?.base_url === "string"
              ? recipe.model_configuration.base_url
              : undefined,
        }
      : null;
  const options = useQuery({
    queryKey: ["agent-native-tool-options", request],
    enabled: !!request,
    queryFn: ({ signal }) =>
      result(client.POST("/api/models/options", { body: request!, signal })),
  });
  const capabilities = Array.isArray(raw?.capabilities) ? raw.capabilities : [];
  const configured = capabilities.map(nativeToolKind).filter(Boolean);
  const tools = options.data?.native_tools ?? [];
  const knownText: Record<string, string> = {
    "Web Search \u00b7 native": t("Web Search - native"),
    "Provider search and citations; provider usage charges may apply.": t(
      "Provider search and citations; provider usage charges may apply.",
    ),
    "Web Fetch \u00b7 native": t("Web Fetch - native"),
    "Provider reads URLs in model context; not a crawler or local download.": t(
      "Provider reads URLs in model context; not a crawler or local download.",
    ),
    "X Search \u00b7 native": t("X Search - native"),
    "Search X posts through xAI; separate from general web search.": t(
      "Search X posts through xAI; separate from general web search.",
    ),
    "Image Generation \u00b7 native": t("Image Generation - native"),
    "Save generated images to Thread tmp; return file paths. Uses image quota.":
      t(
        "Save generated images to Thread tmp; return file paths. Uses image quota.",
      ),
    "Code Execution \u00b7 native": t("Code Execution - native"),
    "Provider sandbox, not your workspace shell; files stay with the provider.":
      t(
        "Provider sandbox, not your workspace shell; files stay with the provider.",
      ),
    "Remote MCP \u00b7 provider-hosted": t("Remote MCP - provider-hosted"),
    "Requires a public remote server URL and label; provider connects, not the Host.":
      t(
        "Requires a public remote server URL and label; provider connects, not the Host.",
      ),
    "File Search \u00b7 native": t("File Search - native"),
    "Requires provider stores; no local uploads. Google: deselect other native tools first.":
      t(
        "Requires provider stores; no local uploads. Google: deselect other native tools first.",
      ),
    "Advisor \u00b7 native": t("Advisor - native"),
    "Requires an advisor model ID; additional model usage is billed by the provider.":
      t(
        "Requires an advisor model ID; additional model usage is billed by the provider.",
      ),
  };
  return (
    <>
      <ErrorNotice error={saved.error || options.error} />
      {!raw?.model ? (
        <p>{t("Choose a saved model to see its tools.")}</p>
      ) : options.isPending && request ? (
        <p>{t("Loading tool support\u2026")}</p>
      ) : !tools.length ? (
        <p>{t("This model has no built-in tool options.")}</p>
      ) : (
        tools.map((tool) => (
          <SettingsToggle
            key={tool.value}
            label={knownText[tool.label] ?? tool.label}
            description={[
              knownText[tool.description] ?? tool.description,
              tool.recommended && t("Recommended for this model."),
              !tool.capability &&
                t("Configure required provider parameters in advanced YAML."),
            ]
              .filter(Boolean)
              .join(" ")}
            checked={configured.includes(tool.value)}
            disabled={!tool.capability}
            onCheckedChange={(checked) =>
              onChange(toggleNativeTool(source, tool, checked))
            }
          />
        ))
      )}
      {options.data &&
        configured.some(
          (kind: string) => !tools.some((tool) => tool.value === kind),
        ) && (
          <p role="status">
            {t(
              "Some saved tools aren't available for this model. Review them in Capabilities or Advanced configuration.",
            )}
          </p>
        )}
    </>
  );
}
