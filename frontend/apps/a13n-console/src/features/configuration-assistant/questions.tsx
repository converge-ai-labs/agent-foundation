import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Textarea } from "a13n-ui";
import type { Schema } from "../../shared/api";
import styles from "./questions.module.css";

type Question = {
  header: string;
  question: string;
  options: { label: string; description: string }[];
  multiSelect: boolean;
};

export function readQuestions(
  value: Schema["JsonValue"] | undefined,
): Question[] | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (!("questions" in value)) return null;
  if (!Array.isArray(value.questions) || !value.questions.length) return null;
  const questions: Question[] = [];
  for (const item of value.questions) {
    if (
      !item ||
      typeof item !== "object" ||
      Array.isArray(item) ||
      typeof item.question !== "string" ||
      typeof item.header !== "string" ||
      !Array.isArray(item.options) ||
      item.options.length < 2
    )
      return null;
    const options: Question["options"] = [];
    for (const option of item.options) {
      if (
        !option ||
        typeof option !== "object" ||
        Array.isArray(option) ||
        typeof option.label !== "string" ||
        typeof option.description !== "string"
      )
        return null;
      options.push({ label: option.label, description: option.description });
    }
    questions.push({
      header: item.header,
      question: item.question,
      options,
      multiSelect: item.multiSelect === true,
    });
  }
  return questions;
}

export function QuestionResponse({
  questions,
  onChange,
}: {
  questions: Question[];
  onChange: (answer: {
    action: string;
    value: string;
    structured: boolean;
  }) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [values, setValues] = useState<
    Record<string, { selected: string[]; custom: string; other: boolean }>
  >({});
  const [skipped, setSkipped] = useState(false);
  function update(
    question: string,
    value: { selected: string[]; custom: string; other: boolean },
  ) {
    const next = { ...values, [question]: value };
    setValues(next);
    setSkipped(false);
    const answers: Record<string, string | string[]> = {};
    for (const q of questions) {
      const v = next[q.question];
      if (v?.other && v.custom.trim()) answers[q.question] = v.custom.trim();
      else if (v && !v.other && v.selected.length)
        answers[q.question] = q.multiSelect ? v.selected : v.selected[0]!;
    }
    onChange({
      action: Object.keys(answers).length === questions.length ? "respond" : "",
      value: JSON.stringify({ answers }),
      structured: true,
    });
  }
  return (
    <div className={styles.questions}>
      {questions.map((q, index) => {
        const value = values[q.question] ?? {
          selected: [],
          custom: "",
          other: false,
        };
        return (
          <fieldset key={q.question} className={styles.question}>
            <legend>
              <span className={styles.header}>{q.header}</span>
              <span>{q.question}</span>
            </legend>
            <p className={styles.hint}>
              {t(
                q.multiSelect
                  ? "Select one or more answers, or write your own."
                  : "Select one answer, or write your own.",
              )}
            </p>
            <div className={styles.options}>
              {q.options.map((option) => (
                <label key={option.label} className={styles.option}>
                  <input
                    type={q.multiSelect ? "checkbox" : "radio"}
                    name={`${id}-${index}`}
                    checked={
                      !value.other && value.selected.includes(option.label)
                    }
                    onChange={() =>
                      update(q.question, {
                        ...value,
                        other: false,
                        selected: q.multiSelect
                          ? value.selected.includes(option.label)
                            ? value.selected.filter((v) => v !== option.label)
                            : [...value.selected, option.label]
                          : [option.label],
                      })
                    }
                  />
                  <span>
                    <span className={styles.label}>{option.label}</span>
                    <span className={styles.description}>
                      {option.description}
                    </span>
                  </span>
                </label>
              ))}
              <label className={styles.option}>
                <input
                  type={q.multiSelect ? "checkbox" : "radio"}
                  name={`${id}-${index}`}
                  checked={value.other}
                  onChange={() =>
                    update(q.question, {
                      selected: [],
                      custom: value.custom,
                      other: !value.other,
                    })
                  }
                />
                <span className={styles.label}>
                  {t("Write your own answer")}
                </span>
              </label>
            </div>
            {value.other && (
              <Textarea
                aria-label={`${q.header}: ${t("Your answer")}`}
                value={value.custom}
                onChange={(event) =>
                  update(q.question, { ...value, custom: event.target.value })
                }
              />
            )}
          </fieldset>
        );
      })}
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-pressed={skipped}
        onClick={() => {
          setSkipped(true);
          onChange({ action: "omit", value: "", structured: true });
        }}
      >
        {t("Continue without a response")}
      </Button>
      {skipped && (
        <p role="status" className={styles.hint}>
          {t("These questions will be skipped when you submit responses.")}
        </p>
      )}
    </div>
  );
}
