import { useId, useState, type ReactNode } from "react";
import { Button } from "../components/button";
import { Input } from "../components/input";
import { FormField } from "./form-field";
import { ChoiceField } from "./choice-field";
import styles from "./mcp-elicitation.module.css";

export type McpElicitationPrompt = {
  message: string;
  server_id: string;
  server_name?: string | null;
  source?: "mcp" | "acp";
  mode: "form" | "url";
  schema?: unknown;
  url?: string | null;
  url_host?: string | null;
};
export type McpElicitationResponse = {
  action: "accept" | "decline" | "cancel";
  content?: Record<string, unknown>;
};

type Copy =
  | "Input requested"
  | "MCP input request"
  | "Agent input request"
  | "External destination"
  | "Open external page"
  | "Finish the action in your browser, then choose Accept. Server credentials aren't sent to that page."
  | "Your response goes to this MCP server. Don't include passwords, tokens or other secrets."
  | "Your response goes to this Agent. Don't include passwords, tokens or other secrets."
  | "Select an option"
  | "Yes"
  | "No"
  | "Accept"
  | "Decline"
  | "Cancel request"
  | "This field is required."
  | "Enter a valid value for this field.";

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function enumOptions(field: Record<string, unknown>) {
  if (Array.isArray(field.enum))
    return field.enum.map((value, index) => ({
      value,
      label: Array.isArray(field.enumNames)
        ? String(field.enumNames[index] ?? value)
        : String(value),
    }));
  const titled = field.oneOf ?? field.anyOf;
  if (Array.isArray(titled))
    return titled.map((item) => ({
      value: record(item)?.const,
      label: String(record(item)?.title ?? record(item)?.const),
    }));
  return undefined;
}

function valid(field: Record<string, unknown>, value: unknown) {
  const min = (bound: unknown, actual: number) =>
    typeof bound !== "number" || actual >= bound;
  const max = (bound: unknown, actual: number) =>
    typeof bound !== "number" || actual <= bound;
  if (Array.isArray(value))
    return (
      min(field.minItems, value.length) && max(field.maxItems, value.length)
    );
  if (typeof value === "number")
    return (
      Number.isFinite(value) &&
      (field.type !== "integer" || Number.isInteger(value)) &&
      min(field.minimum, value) &&
      max(field.maximum, value)
    );
  if (typeof value === "string") {
    const length = Array.from(value).length;
    if (!min(field.minLength, length) || !max(field.maxLength, length))
      return false;
  }
  return true;
}

export function McpElicitationForm({
  request,
  send,
  locked,
  t,
  context,
  feedback,
}: {
  request: McpElicitationPrompt;
  send: (response: McpElicitationResponse) => void;
  locked: boolean;
  t: (copy: Copy) => string;
  context?: ReactNode;
  feedback?: ReactNode;
}) {
  const formId = useId();
  const [checked, setChecked] = useState(false);
  const schema = record(request.schema);
  const properties = record(schema?.properties) ?? {};
  const required = Array.isArray(schema?.required) ? schema.required : [];
  const [content, setContent] = useState<
    NonNullable<McpElicitationResponse["content"]>
  >(() =>
    Object.fromEntries(
      Object.entries(properties).flatMap(([name, value]) => {
        const field = record(value);
        return field?.default === undefined ? [] : [[name, field.default]];
      }),
    ),
  );
  const errors = Object.fromEntries(
    Object.entries(properties).flatMap(([name, schema]) => {
      const value = content[name];
      if (value === undefined)
        return required.includes(name)
          ? [[name, t("This field is required.")]]
          : [];
      return valid(record(schema) ?? {}, value)
        ? []
        : [[name, t("Enter a valid value for this field.")]];
    }),
  );
  const isAcp = request.source === "acp";
  return (
    <section
      className={styles.request}
      aria-label={isAcp ? t("Agent input request") : t("MCP input request")}
    >
      <header className={styles.heading}>
        <h3>{t("Input requested")}</h3>
        <span className={styles.hint}>
          {request.server_name ?? request.server_id}
        </span>
      </header>
      {context}
      <p className={styles.reason}>{request.message}</p>
      {request.mode === "url" ? (
        <div className={styles.destination}>
          <p className={styles.hint}>{t("External destination")}</p>
          <strong>{request.url_host}</strong>
          <a
            href={request.url ?? undefined}
            target="_blank"
            rel="noopener noreferrer"
          >
            {t("Open external page")}
          </a>
          <p className={styles.hint}>
            {t(
              "Finish the action in your browser, then choose Accept. Server credentials aren't sent to that page.",
            )}
          </p>
        </div>
      ) : (
        <form
          id={formId}
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            if (locked) return;
            setChecked(true);
            if (!Object.keys(errors).length)
              send({ action: "accept", content });
          }}
        >
          <div className={styles.request}>
            <p className={styles.hint}>
              {isAcp
                ? t(
                    "Your response goes to this Agent. Don't include passwords, tokens or other secrets.",
                  )
                : t(
                    "Your response goes to this MCP server. Don't include passwords, tokens or other secrets.",
                  )}
            </p>
            {Object.entries(properties).map(([name, value]) => {
              const field = record(value) ?? {};
              const label =
                typeof field.title === "string" ? field.title : name;
              const description =
                typeof field.description === "string"
                  ? field.description
                  : undefined;
              const set = (value: unknown) =>
                setContent((old) => {
                  const next = { ...old };
                  if (value === undefined) delete next[name];
                  else next[name] = value;
                  return next;
                });
              const error = checked ? errors[name] : undefined;
              const enums = enumOptions(field);
              const items = record(field.items);
              const selections = items ? enumOptions(items) : undefined;
              if (field.type === "array" && selections)
                return (
                  <fieldset key={name} disabled={locked}>
                    <legend>{label}</legend>
                    {description && (
                      <p className={styles.hint}>{description}</p>
                    )}
                    {selections.map(({ value: option, label: title }) => (
                      <label key={String(option)} className={styles.selection}>
                        <input
                          type="checkbox"
                          checked={
                            Array.isArray(content[name]) &&
                            content[name].includes(option)
                          }
                          onChange={(event) => {
                            const selected = Array.isArray(content[name])
                              ? content[name]
                              : [];
                            set(
                              event.target.checked
                                ? [...selected, option]
                                : selected.filter((item) => item !== option),
                            );
                          }}
                        />
                        {title}
                      </label>
                    ))}
                    {error && (
                      <p role="alert" className={styles.error}>
                        {error}
                      </p>
                    )}
                  </fieldset>
                );
              if (field.type === "boolean" || enums)
                return (
                  <ChoiceField
                    key={name}
                    label={label}
                    description={description}
                    error={error}
                    required={required.includes(name)}
                    disabled={locked}
                    placeholder={t("Select an option")}
                    value={
                      content[name] === undefined
                        ? undefined
                        : String(content[name])
                    }
                    options={(
                      enums ?? [
                        { value: true, label: t("Yes") },
                        { value: false, label: t("No") },
                      ]
                    ).map((option) => ({
                      value: String(option.value),
                      label: option.label,
                    }))}
                    onValueChange={(selected) =>
                      set(
                        (
                          enums ?? [
                            { value: true, label: t("Yes") },
                            { value: false, label: t("No") },
                          ]
                        ).find((option) => String(option.value) === selected)
                          ?.value,
                      )
                    }
                  />
                );
              return (
                <FormField
                  key={name}
                  label={label}
                  description={description}
                  error={error}
                >
                  <Input
                    aria-label={label}
                    disabled={locked}
                    required={required.includes(name)}
                    type={
                      field.type === "number" || field.type === "integer"
                        ? "number"
                        : "text"
                    }
                    step={
                      field.type === "integer"
                        ? 1
                        : field.type === "number"
                          ? "any"
                          : undefined
                    }
                    value={
                      content[name] === undefined ? "" : String(content[name])
                    }
                    onChange={(event) =>
                      set(
                        event.target.value === ""
                          ? undefined
                          : field.type === "number" || field.type === "integer"
                            ? event.target.valueAsNumber
                            : event.target.value,
                      )
                    }
                  />
                </FormField>
              );
            })}
          </div>
        </form>
      )}
      <div className={styles.actions}>
        {request.mode === "url" ? (
          <Button disabled={locked} onClick={() => send({ action: "accept" })}>
            {t("Accept")}
          </Button>
        ) : (
          <Button type="submit" form={formId} disabled={locked}>
            {t("Accept")}
          </Button>
        )}
        <Button
          variant="ghost"
          disabled={locked}
          onClick={() => send({ action: "decline" })}
        >
          {t("Decline")}
        </Button>
        <Button
          variant="ghost"
          disabled={locked}
          onClick={() => send({ action: "cancel" })}
        >
          {t("Cancel request")}
        </Button>
      </div>
      {feedback}
    </section>
  );
}
