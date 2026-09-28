import { CheckIcon } from "@phosphor-icons/react";
import { Button, Input } from "a13n-ui";
import { useState } from "react";
import type { Schema } from "../transport/client";
import styles from "./model-picker.module.css";

/** Searchable model choices within the Agent & Model panel. */
export function ModelOptions({
  models,
  defaultModelId,
  defaultSource = "agent",
  value,
  disabled,
  onChange,
  autoFocus = false,
}: {
  autoFocus?: boolean;
  models: Schema<"ModelSummary">[];
  defaultModelId?: string;
  defaultSource?: "agent" | "thread";
  value?: string;
  disabled?: boolean;
  onChange: (value: string | undefined) => void;
}) {
  const [query, setQuery] = useState("");
  const defaultModel = models.find((item) => item.model_id === defaultModelId);
  const model = models.find(
    (item) => item.model_id === (value ?? defaultModelId),
  );
  const names = new Map<string, number>();
  for (const item of models)
    names.set(item.name, (names.get(item.name) ?? 0) + 1);
  const filtered = models.filter((item) =>
    `${item.name} ${item.model_id} ${item.route}`
      .toLocaleLowerCase()
      .includes(query.trim().toLocaleLowerCase()),
  );

  return (
    <>
      <Input
        autoFocus={autoFocus}
        className={styles.search}
        aria-label="Search models"
        placeholder="Search models…"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <div className={styles.modelList} aria-label="Models">
        <Button
          variant="ghost"
          disabled={disabled}
          className={styles.modelOption}
          aria-pressed={value === undefined}
          onClick={() => onChange(undefined)}
        >
          <span>
            {defaultSource === "thread" ? "Thread default" : "Agent default"}
            <small>
              {defaultModel
                ? `Currently ${defaultModel.name}`
                : "No default model available"}
            </small>
          </span>
          {value === undefined && <CheckIcon aria-hidden />}
        </Button>
        <div className={styles.divider} />
        {filtered.map((item) => (
          <Button
            key={item.model_id}
            variant="ghost"
            disabled={disabled}
            className={styles.modelOption}
            aria-pressed={value === item.model_id}
            title={`${item.model_id} · ${item.route}`}
            onClick={() => onChange(item.model_id)}
          >
            <span>
              {item.name}
              {(names.get(item.name) ?? 0) > 1 && (
                <small>
                  {item.model_id} · {item.route}
                </small>
              )}
            </span>
            {value === item.model_id && <CheckIcon aria-hidden />}
          </Button>
        ))}
        {filtered.length === 0 && (
          <p className={styles.hint}>No models found.</p>
        )}
        {value && !model && (
          <p className={styles.hint}>{value} (unavailable)</p>
        )}
      </div>
    </>
  );
}
