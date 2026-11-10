import { useTranslation } from "../i18n";
import { modelControlMessage } from "./model-control-messages";
import { markColors } from "a13n-ui/brand/mark";
import { Button, ToggleGroup, ToggleGroupItem } from "a13n-ui";
import { useState, useRef, useEffect, type CSSProperties } from "react";
import type { TFunction } from "i18next";
import { ArrowCounterClockwise } from "@phosphor-icons/react";
import type { Schema } from "../transport/client";
import { SettingsHeader } from "./composer-settings";
import styles from "./composer-settings.module.css";

type Selection = Schema<"SubmitRequest">["thinking"];

export function thinkingLabel(
  model: Schema<"ModelSummary"> | undefined,
  value: Selection,
  t: TFunction,
) {
  if (value == null) return t("Default");
  const option = model?.thinking?.options.find((item) => item.value === value);
  return modelControlMessage(option?.label ?? "Unavailable selection", t);
}

/** Provider rules and native values belong to the backend catalog, not React. */
export function ThinkingPicker({
  model,
  value,
  disabled,
  onChange,
}: {
  model?: Schema<"ModelSummary">;
  value?: Selection;
  disabled?: boolean;
  onChange: (value: Selection) => void;
}) {
  const { t } = useTranslation();
  const control = model?.thinking;
  const options = control?.options ?? [];
  const explicit = options.filter((item) => item.value !== null);
  const inherited = options.find((item) => item.value === null);
  const selected = options.find((item) => item.value === (value ?? null));
  const unavailable =
    value != null && (!selected || !!selected.disabled_reason);
  const [drag, setDrag] = useState<number | null>(null);
  const current = explicit.findIndex((item) => item.value === value);
  const active = drag === null ? current : Math.round(drag);
  const level = explicit[active];
  const label = thinkingLabel(model, level?.value ?? value, t);
  const gesture = useRef<AbortController | null>(null);
  const handledPointer = useRef(false);
  useEffect(() => {
    setDrag(null);
    return () => gesture.current?.abort();
  }, [model?.model_id, disabled]);
  function startDrag(event: React.PointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    if (disabled || !event.isPrimary || event.button !== 0 || !rect.width)
      return;
    event.preventDefault();
    const inset =
      parseFloat(
        getComputedStyle(event.currentTarget).getPropertyValue("--rail-height"),
      ) / 2;
    const indexAt = (x: number) =>
      Math.max(
        0,
        Math.min(
          explicit.length - 1,
          ((x - rect.left - inset) / Math.max(1, rect.width - 2 * inset)) *
            (explicit.length - 1),
        ),
      );
    gesture.current?.abort();
    const controller = new AbortController();
    gesture.current = controller;
    const options = { signal: controller.signal };
    const pointerId = event.pointerId;
    handledPointer.current = true;
    setDrag(indexAt(event.clientX));
    function finish() {
      controller.abort();
      setDrag(null);
    }
    window.addEventListener(
      "pointermove",
      (e) => {
        if (e.pointerId === pointerId) setDrag(indexAt(e.clientX));
      },
      options,
    );
    window.addEventListener(
      "pointerup",
      (e) => {
        if (e.pointerId !== pointerId) return;
        const item = explicit[Math.round(indexAt(e.clientX))];
        finish();
        if (item && !item.disabled_reason) onChange(item.value);
      },
      options,
    );
    window.addEventListener(
      "pointercancel",
      (e) => {
        if (e.pointerId === pointerId) finish();
      },
      options,
    );
    window.addEventListener("blur", finish, options);
  }
  return (
    <section className={styles.effortPanel} aria-label={t("Thinking")}>
      <SettingsHeader
        accessibleTitle={t("Thinking")}
        title={
          <span key={label} className={styles.effortValue}>
            {label}
          </span>
        }
        action={
          <Button
            variant="ghost"
            size="icon-sm"
            className={styles.resetEffort}
            aria-label={t("Use default thinking")}
            aria-pressed={value == null}
            disabled={disabled || value == null || !!inherited?.disabled_reason}
            title={modelControlMessage(
              inherited?.disabled_reason ??
                control?.default_summary ??
                inherited?.description ??
                "Default",
              t,
            )}
            onClick={() => onChange(null)}
          >
            <ArrowCounterClockwise aria-hidden />
          </Button>
        }
      />
      {explicit.length > 0 && (
        <div
          className={styles.effortTrack}
          style={
            {
              "--mark-face": markColors.face,
              "--mark-shadow": markColors.shadow,
              "--progress":
                Math.max(0, drag ?? current) / Math.max(1, explicit.length - 1),
            } as CSSProperties
          }
          data-active={active >= 0}
          data-dragging={drag !== null}
          onPointerDownCapture={startDrag}
          onClickCapture={(event) => {
            if (handledPointer.current && event.detail > 0)
              event.stopPropagation();
            handledPointer.current = false;
          }}
        >
          <div aria-hidden className={styles.effortFinish} />
          <ToggleGroup
            className={styles.stops}
            aria-description={t(
              "Higher thinking levels spend more time and tokens on a response. Available levels depend on the selected model. Default uses the model’s saved setting.",
            )}
            aria-label={t("Thinking level")}
            value={value == null ? [] : [JSON.stringify(value)]}
            disabled={disabled}
            onValueChange={(keys) => {
              const option =
                explicit.find(
                  (item) => JSON.stringify(item.value) === keys[0],
                ) ?? selected;
              if (option && !option.disabled_reason) onChange(option.value);
            }}
          >
            {explicit.map((item, index) => {
              return (
                <ToggleGroupItem
                  key={JSON.stringify(item.value)}
                  value={JSON.stringify(item.value)}
                  aria-label={modelControlMessage(item.label, t)}
                  data-filled={index <= active}
                  disabled={!!item.disabled_reason}
                  title={
                    item.disabled_reason
                      ? modelControlMessage(item.disabled_reason, t)
                      : modelControlMessage(item.label, t) +
                        " · " +
                        modelControlMessage(item.description, t)
                  }
                >
                  <span aria-hidden className={styles.tick} />
                  {explicit.length > 1 &&
                    (index === 0 || index === explicit.length - 1) && (
                      <span aria-hidden className={styles.rangeLabel}>
                        {modelControlMessage(item.label, t)}
                      </span>
                    )}
                </ToggleGroupItem>
              );
            })}
          </ToggleGroup>
        </div>
      )}
      {unavailable && (
        <p role="alert" className={styles.hint}>
          {selected?.disabled_reason != null
            ? modelControlMessage(selected.disabled_reason, t)
            : t("Unavailable selection — use default.")}
        </p>
      )}
      {(!control || control.reason) && (
        <p className={styles.hint}>
          {control?.reason != null
            ? modelControlMessage(control.reason, t)
            : t("No thinking controls available.")}
        </p>
      )}
    </section>
  );
}
