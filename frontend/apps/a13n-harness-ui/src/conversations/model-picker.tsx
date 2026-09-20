import {
  CaretDownIcon,
  CaretLeftIcon,
  CaretRightIcon,
  CheckIcon,
} from "@phosphor-icons/react";
import {
  Button,
  Input,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from "a13n-ui";
import { useEffect, useRef, useState } from "react";
import type { Schema } from "../transport/client";
import { ThinkingPicker, thinkingSummary } from "./thinking-picker";
import { FastToggle } from "./fast-toggle";
import styles from "./model-picker.module.css";

export function ModelPicker({
  models,
  defaultModelId,
  value,
  defaultSource = "agent",
  thinking,
  fast,
  onFastChange,
  disabled,
  onChange,
  onThinkingChange,
}: {
  models: Schema<"ModelSummary">[];
  defaultModelId?: string;
  defaultSource?: "agent" | "thread";
  value?: string;
  thinking?: Schema<"SubmitRequest">["thinking"];
  fast?: Schema<"SubmitRequest">["fast"];
  onFastChange: (value: boolean) => void;
  disabled?: boolean;
  onChange: (value: string | undefined) => void;
  onThinkingChange: (value: Schema<"SubmitRequest">["thinking"]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [choosingModel, setChoosingModel] = useState(false);
  const [query, setQuery] = useState("");
  const modelButton = useRef<HTMLButtonElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const defaultModel = models.find((item) => item.model_id === defaultModelId);
  const model = models.find(
    (item) => item.model_id === (value ?? defaultModelId),
  );
  const modelName =
    model?.name ?? `${value ?? defaultModelId ?? "Model"} (unavailable)`;
  const summary = thinkingSummary(model, thinking);
  const names = new Map<string, number>();
  for (const item of models)
    names.set(item.name, (names.get(item.name) ?? 0) + 1);
  const filtered = models.filter((item) =>
    `${item.name} ${item.model_id} ${item.route}`
      .toLocaleLowerCase()
      .includes(query.trim().toLocaleLowerCase()),
  );

  useEffect(() => {
    if (open) (choosingModel ? searchInput : modelButton).current?.focus();
  }, [open, choosingModel]);

  useEffect(() => {
    if (disabled) {
      setOpen(false);
      setChoosingModel(false);
      setQuery("");
    }
  }, [disabled]);

  function selectModel(next: string | undefined) {
    onChange(next);
    setChoosingModel(false);
    setQuery("");
  }

  return (
    <Popover
      open={open && !disabled}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setChoosingModel(false);
          setQuery("");
        }
      }}
    >
      <PopoverTrigger
        render={<Button variant="outline" size="sm" />}
        className={styles.trigger}
        disabled={disabled}
        aria-label="Model settings"
        title={[modelName, summary].filter(Boolean).join(" · ")}
      >
        <span className={styles.modelName}>{modelName}</span>
        {summary && <span className={styles.summary}>{summary}</span>}
        <CaretDownIcon aria-hidden />
      </PopoverTrigger>
      <PopoverPopup side="top" align="start" className={styles.popup}>
        {choosingModel ? (
          <>
            <div className={styles.heading}>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Back to model settings"
                onClick={() => {
                  setChoosingModel(false);
                  setQuery("");
                }}
              >
                <CaretLeftIcon />
              </Button>
              <PopoverTitle className={styles.title}>Select model</PopoverTitle>
            </div>
            <Input
              ref={searchInput}
              className={styles.search}
              aria-label="Search models"
              placeholder="Search models…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <div className={styles.modelList} aria-label="Models">
              <Button
                variant="ghost"
                className={styles.modelOption}
                aria-pressed={value === undefined}
                onClick={() => selectModel(undefined)}
              >
                <span>
                  {defaultSource === "thread"
                    ? "Thread default"
                    : "Agent default"}
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
                  className={styles.modelOption}
                  aria-pressed={value === item.model_id}
                  title={`${item.model_id} · ${item.route}`}
                  onClick={() => selectModel(item.model_id)}
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
        ) : (
          <>
            <PopoverTitle className={styles.title}>Model</PopoverTitle>
            <div className={styles.modelSettings}>
              <FastToggle
                model={model}
                value={fast}
                disabled={disabled}
                onChange={onFastChange}
              />
              <Button
                ref={modelButton}
                variant="ghost"
                className={styles.modelOption}
                aria-label="Change model"
                onClick={() => setChoosingModel(true)}
              >
                <span>{modelName}</span>
                <CaretRightIcon aria-hidden />
              </Button>
            </div>
            <p className={styles.hint}>
              {value === undefined
                ? `Following ${defaultSource} default`
                : "Override for next run"}
            </p>
            <ThinkingPicker
              model={model}
              value={thinking}
              disabled={disabled}
              onChange={onThinkingChange}
            />
          </>
        )}
      </PopoverPopup>
    </Popover>
  );
}
