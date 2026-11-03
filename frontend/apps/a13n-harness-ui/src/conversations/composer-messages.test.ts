import { expect, it } from "vitest";
import { i18n } from "../i18n";
import { composerMessage, stopMessage } from "./composer-messages";

it("translates known feedback while retaining interpolated identifiers and unknown diagnostics", () => {
  const t = i18n.getFixedT("zh-CN");
  expect(
    composerMessage("{{name}} exceeds 10 MiB.", t, {
      name: "/tmp/My Report.md",
    }),
  ).toBe("/tmp/My Report.md 超过 10 MiB。");
  const diagnostic =
    "vendor_raw: {{identifier}} quota exceeded /srv/source.txt";
  expect(composerMessage(diagnostic, t)).toBe(diagnostic);
  expect(
    composerMessage("Could not load skills: {{message}}", t, {
      message: diagnostic,
    }),
  ).toBe(`无法加载技能：${diagnostic}`);
});

it.each([
  ["completed", "操作已完成。正在刷新聊天…"],
  ["suspended", "操作已挂起。正在刷新聊天…"],
  ["failed", "操作已失败。正在刷新聊天…"],
  ["cancelled", "操作已停止。正在刷新聊天…"],
] as const)(
  "shows the observed %s outcome instead of claiming every Stop cancelled execution",
  (outcome, expected) => {
    expect(
      stopMessage(
        {
          receipt: "receipt-one",
          attempt: 1,
          phase: "waiting",
          message: "Stopping… Waiting for execution and cleanup to finish.",
          outcome,
        },
        i18n.getFixedT("zh-CN"),
      ),
    ).toBe(expected);
  },
);
