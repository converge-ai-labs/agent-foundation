import { useState } from "react";
import { TextField } from "../shell/ui";
import type { Schema } from "../transport/client";
import type { DecisionResponse } from "./decision-inputs";
import styles from "./conversation.module.css";

export function QuestionInput({
  request,
  onChange,
}: {
  request: Schema<"StructuredQuestionRequestView">;
  onChange: (response: DecisionResponse | undefined) => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string | string[]>>({});
  const answer = (question: string, value: string | string[]) => {
    const next = { ...answers, [question]: value };
    setAnswers(next);
    if (
      request.questions.every((item) =>
        typeof next[item.question] === "string"
          ? (next[item.question] as string).trim()
          : (next[item.question] as string[] | undefined)?.length,
      )
    )
      onChange({
        kind: "question",
        request_id: request.request_id,
        answers: next,
      });
    else onChange(undefined);
  };
  return (
    <div className={styles.form}>
      {request.questions.map((question) => (
        <fieldset key={question.question} className={styles.question}>
          <legend>{question.header}</legend>
          <p>{question.question}</p>
          {question.options.map((option) => (
            <label key={option.label} className={styles.answerOption}>
              <input
                type={question.multi_select ? "checkbox" : "radio"}
                name={`${request.request_id}:${question.question}`}
                checked={
                  question.multi_select
                    ? Array.isArray(answers[question.question]) &&
                      answers[question.question].includes(option.label)
                    : answers[question.question] === option.label
                }
                onChange={(event) => {
                  if (!question.multi_select)
                    answer(question.question, option.label);
                  else {
                    const selected = Array.isArray(answers[question.question])
                      ? (answers[question.question] as string[])
                      : [];
                    answer(
                      question.question,
                      event.target.checked
                        ? [...selected, option.label]
                        : selected.filter((value) => value !== option.label),
                    );
                  }
                }}
              />
              <span>
                <strong>{option.label}</strong>{" "}
                <small>{option.description}</small>
              </span>
            </label>
          ))}
          <TextField
            label="Or write your own answer"
            value={
              typeof answers[question.question] === "string" &&
              !question.options.some(
                (option) => option.label === answers[question.question],
              )
                ? (answers[question.question] as string)
                : ""
            }
            onChange={(value) => answer(question.question, value)}
          />
        </fieldset>
      ))}
    </div>
  );
}
