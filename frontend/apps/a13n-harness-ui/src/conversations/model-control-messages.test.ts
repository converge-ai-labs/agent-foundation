import { expect, it } from "vitest";
import { i18n } from "../i18n";
import en from "../i18n/locales/en.json";
import zhCN from "../i18n/locales/zh-CN.json";
import {
  modelControlMessage,
  modelControlMessages,
} from "./model-control-messages";
import { speedLabel } from "./speed-label";
import { reasoningModeLabel } from "./reasoning-mode-picker";
import type { Schema } from "../transport/client";

it("covers known control descriptors and every dynamic speed/reasoning label in both catalogs", () => {
  const speeds: Record<Schema<"FastControl">["state"], true> = {
    on: true,
    off: true,
    default: true,
    ultrafast: true,
  };
  const modes: Record<Schema<"ReasoningModeControl">["state"], true> = {
    standard: true,
    pro: true,
    custom: true,
    default: true,
  };
  const labels = [
    ...modelControlMessages,
    ...Object.keys(speeds).map((state) =>
      speedLabel(state as keyof typeof speeds),
    ),
    ...Object.keys(modes).map((state) =>
      reasoningModeLabel(state as keyof typeof modes),
    ),
    reasoningModeLabel(),
  ];
  for (const label of labels) {
    expect(Object.hasOwn(en, label), label).toBe(true);
    expect(Object.hasOwn(zhCN, label), label).toBe(true);
  }
});

it("localizes exact backend summaries without guessing provider capability or losing numeric values", () => {
  const t = i18n.getFixedT("zh-CN");
  expect(modelControlMessage("Off (requested)", t)).toBe("关闭（已请求）");
  expect(modelControlMessage("Thinking level; not disabled", t)).toBe(
    "思考级别；未关闭思考",
  );
  expect(modelControlMessage("Adaptive · High", t)).toBe("自适应 · 高");
  expect(modelControlMessage("Effort · Custom", t)).toBe("强度 · 自定义");
  expect(modelControlMessage("Budget · 8,192 tokens", t)).toBe(
    "预算 · 8,192 token",
  );
  expect(modelControlMessage("Thinking budget: 16,384 tokens", t)).toBe(
    "思考预算：16,384 token",
  );
  expect(modelControlMessage("Budget · -2 tokens", t)).toBe("预算 · -2 token");
  expect(
    modelControlMessage("Budget · 8,192 tokens", i18n.getFixedT("en")),
  ).toBe("Budget · 8,192 tokens");
  expect(modelControlMessage("Budget · 1 tokens", i18n.getFixedT("en"))).toBe(
    "Budget · 1 token",
  );
  expect(modelControlMessage("Thinking budget: 1 tokens", t)).toBe(
    "思考预算：1 token",
  );
});

it("leaves unknown labels, opaque diagnostics and unrecognized summary formats verbatim", () => {
  for (const message of [
    "vendor_id=42: quota exceeded 原文",
    "Quick",
    "Deep",
    "Adaptive · provider/custom",
    "Budget · 8,192 tokens: raw detail",
    "Budget · 9007199254740993 tokens",
    "Budget · 9,007,199,254,740,993 tokens",
    "Thinking budget: 1,23 tokens",
    "Search models",
  ])
    expect(modelControlMessage(message, i18n.getFixedT("zh-CN"))).toBe(message);
  expect(
    modelControlMessage(
      "Thinking is controlled by extra_body. Edit the Model configuration before using an override.",
      i18n.getFixedT("zh-CN"),
    ),
  ).toContain("extra_body");
});
