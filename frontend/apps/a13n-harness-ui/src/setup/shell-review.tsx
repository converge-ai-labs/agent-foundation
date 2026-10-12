import { useQuery } from "@tanstack/react-query";
import { SearchPicker } from "a13n-ui";
import { connectionId } from "../configuration/model-editor";
import { TextField } from "../shell/ui";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";

export function ShellReviewModel({
  model,
  value,
  onChange,
}: {
  model: Schema<"ModelRecipe">;
  value: string;
  onChange: (value: string) => void;
}) {
  const { client } = useTransport();
  const connections = useQuery({
    queryKey: ["model-choices"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/models/choices", { signal })),
  });
  const connection = connectionId(model, connections.data?.connections ?? []);
  const modelId = model.route.slice(model.route.indexOf(":") + 1);
  const baseUrl = model.model_configuration?.base_url;
  const options = useQuery({
    queryKey: ["shell-review-options", connection, modelId, baseUrl],
    queryFn: ({ signal }) =>
      result(
        client.POST("/api/models/options", {
          body: {
            connection,
            model_id: modelId,
            ...(typeof baseUrl === "string" ? { base_url: baseUrl } : {}),
          },
          signal,
        }),
      ),
    enabled: !!connections.data,
  });
  const candidates = options.data?.shell_review_models ?? [];
  return (
    <>
      <p>
        Review shell commands before execution using a separate Model. Each
        review makes an additional model request. This is not execution
        isolation.
      </p>
      <SearchPicker
        label="Shell review model"
        placeholder="Choose a model (optional)"
        value={value}
        onValueChange={onChange}
        groups={[
          {
            label: "Lightweight models first · same family preferred",
            options: candidates.map((item) => ({
              value: item.value,
              label: item.label,
              description: item.value,
            })),
          },
        ]}
        emptyMessage="Enter a custom model ID below."
      />
      <TextField
        label="Review model ID (optional)"
        value={value}
        onChange={onChange}
        description="Uses the same endpoint and API key as your main Model, with separate settings. Suggestions do not guarantee endpoint access or review quality."
      />
      {options.isError && (
        <p role="status">
          Suggestions are unavailable. Enter a model ID or skip this step.
        </p>
      )}
      <p>
        {value.trim()
          ? `Shell review will use ${value.trim()}. Extra-high risk commands require approval.`
          : "No model selected — shell review will remain off."}{" "}
        Existing root shell review settings stay unchanged.
      </p>
    </>
  );
}
