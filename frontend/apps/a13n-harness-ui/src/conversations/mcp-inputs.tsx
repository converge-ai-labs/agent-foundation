import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, ChoiceField } from "a13n-ui";
import { useTransport } from "../transport/context";
import {
  ApiError,
  isConnectionError,
  result,
  type Schema,
} from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import styles from "./decisions.module.css";
import conversation from "./conversation.module.css";
import { record } from "./decision-evidence";

type Request = Schema<"McpInputRequestView">;
type Response = Schema<"McpInputResponse">;

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

export function useMcpInputRequests(threadId: string, enabled = true) {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["thread", threadId, "mcp-inputs"],
    enabled,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/mcp/inputs", {
          params: { path: { thread_id: threadId } },
          signal,
        }),
      ),
  });
}

export function McpInputs({
  threadId,
  requests,
  reconcile,
}: {
  threadId: string;
  requests?: Request[];
  reconcile: () => void;
}) {
  return (
    <>
      {requests
        ?.filter((request) => request.state === "pending")
        .map((request) => (
          <McpInput
            key={request.request_id}
            threadId={threadId}
            request={request}
            reconcile={reconcile}
          />
        ))}
    </>
  );
}

function McpInput({
  threadId,
  request,
  reconcile,
}: {
  threadId: string;
  request: Request;
  reconcile: () => void;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const schema = record(request.schema);
  const properties = record(schema?.properties) ?? {};
  const required = Array.isArray(schema?.required) ? schema.required : [];
  const [content, setContent] = useState<NonNullable<Response["content"]>>(() =>
    Object.fromEntries(
      Object.entries(properties).flatMap(([name, value]) => {
        const field = record(value);
        return field?.default === undefined ? [] : [[name, field.default]];
      }),
    ),
  );
  const [submitted, setSubmitted] = useState<Response>();
  const answer = useMutation({
    mutationFn: (response: Response) =>
      result(
        client.POST(
          "/api/threads/{thread_id}/mcp/inputs/{request_id}/response",
          {
            params: {
              path: { thread_id: threadId, request_id: request.request_id },
            },
            body: response,
          },
        ),
      ),
    onSuccess: () => {
      void queries.invalidateQueries({
        queryKey: ["thread", threadId, "mcp-inputs"],
      });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "mcp_input_invalid")
        setSubmitted(undefined);
      reconcile();
    },
  });
  function send(response: Response) {
    setSubmitted(response);
    answer.mutate(response);
  }
  const locked = answer.isPending || submitted !== undefined;
  return (
    <section className={conversation.decision} aria-label="MCP input request">
      <div className={styles.request}>
        <div className={styles.heading}>
          <h3>Input requested</h3>
          <span className={styles.tool}>{request.server_id}</span>
        </div>
        {request.thread_id !== threadId && (
          <p className={styles.hint}>From child Thread {request.thread_id}</p>
        )}
        <p className={styles.reason}>{request.message}</p>
        <p className={styles.hint}>
          Your response goes to this MCP server. Don't include passwords, tokens
          or other secrets.
        </p>
        {request.mode === "url" ? (
          <>
            <a
              href={request.url ?? undefined}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open external page
            </a>
            <p className={styles.hint}>
              Finish the action in your browser, then choose Done. Server
              credentials aren't sent to that page.
            </p>
          </>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!locked) send({ action: "accept", content });
            }}
          >
            <div className={styles.request}>
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
                const enums = enumOptions(field);
                const items = record(field.items);
                const selections = items ? enumOptions(items) : undefined;
                if (field.type === "array" && selections)
                  return (
                    <fieldset key={name} disabled={locked}>
                      <legend>{label}</legend>
                      {selections.map(({ value: option, label: title }) => (
                        <label key={String(option)} className={styles.heading}>
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
                    </fieldset>
                  );
                if (field.type === "boolean" || enums)
                  return (
                    <FormField
                      key={name}
                      label={label}
                      description={description}
                    >
                      <ChoiceField
                        label={label}
                        disabled={locked}
                        value={
                          content[name] === undefined
                            ? ""
                            : String(content[name])
                        }
                        options={(
                          enums ?? [
                            { value: true, label: "Yes" },
                            { value: false, label: "No" },
                          ]
                        ).map((option) => ({
                          value: String(option.value),
                          label: option.label,
                        }))}
                        onValueChange={(selected) =>
                          set(
                            (
                              enums ?? [
                                { value: true, label: "Yes" },
                                { value: false, label: "No" },
                              ]
                            ).find(
                              (option) => String(option.value) === selected,
                            )?.value,
                          )
                        }
                      />
                    </FormField>
                  );
                return (
                  <FormField key={name} label={label} description={description}>
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
                          event.target.value === "" && !required.includes(name)
                            ? undefined
                            : field.type === "number" ||
                                field.type === "integer"
                              ? event.target.valueAsNumber
                              : event.target.value,
                        )
                      }
                    />
                  </FormField>
                );
              })}
              <div className={styles.actions}>
                <Button type="submit" disabled={locked}>
                  Send response
                </Button>
              </div>
            </div>
          </form>
        )}
        <div className={styles.actions}>
          {request.mode === "url" && (
            <Button
              disabled={locked}
              onClick={() => send({ action: "accept" })}
            >
              Done
            </Button>
          )}
          <Button
            variant="ghost"
            disabled={locked}
            onClick={() => send({ action: "decline" })}
          >
            Decline
          </Button>
          <Button
            variant="ghost"
            disabled={locked}
            onClick={() => send({ action: "cancel" })}
          >
            Cancel request
          </Button>
        </div>
        {answer.isPending && (
          <p role="status" className={styles.hint}>
            Sending response…
          </p>
        )}
        {answer.error && <ErrorNotice error={answer.error} />}
        {answer.error &&
          (!(answer.error instanceof ApiError) ||
            isConnectionError(answer.error)) && (
            <>
              <p className={styles.hint}>
                Couldn't confirm delivery. Check the request before retrying.
                The tool itself won't run again.
              </p>
              <div className={styles.actions}>
                <Button variant="ghost" onClick={reconcile}>
                  Check request
                </Button>
                <Button
                  variant="ghost"
                  disabled={answer.isPending || !submitted}
                  onClick={() => submitted && answer.mutate(submitted)}
                >
                  Retry response
                </Button>
              </div>
            </>
          )}
      </div>
    </section>
  );
}
