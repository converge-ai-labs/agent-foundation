// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DebugProvider } from "../shell/debug";
import { LanguageChoice, LanguageProvider } from "../i18n/language";
import { i18n } from "../i18n";
import type { Schema, Transport } from "../transport/client";
import { TransportContext } from "../transport/context";
import { ComposerStatus } from "./composer-status";
import { UsageDetails } from "./usage-details";

beforeEach(() => {
  vi.spyOn(navigator, "languages", "get").mockReturnValue(["en-US"]);
});
afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
  delete document.documentElement.dataset.debug;
  await i18n.changeLanguage("en");
});

async function language(name: string) {
  await userEvent.click(screen.getByRole("combobox"));
  await userEvent.click(await screen.findByRole("option", { name }));
}

const root: Schema<"UsageTotals"> = {
  model_requests: 2,
  provider_receipts: 1,
  tokens: [
    ["input_tokens", 12000],
    ["output_tokens", 3456],
    ["cache_read_tokens", 8000],
    ["cache_write_tokens", 1000],
  ],
  model_cost_usd: "0.125",
  unknown_model_costs: 1,
  provider_costs: [["USD", "0.5000"]],
  unknown_provider_costs: 2,
  omitted_currency_receipts: 3,
};
const usage: Schema<"ThreadUsageView"> = {
  thread_id: "one",
  first_observed_at: "2026-01-01T00:00:00Z",
  observed_through: "2026-01-01T00:01:00Z",
  root,
  descendants: { ...root, model_requests: 1 },
  combined: { ...root, model_requests: 3 },
  models: [["provider/root-model", root]],
  other_models: { ...root, model_requests: 1, unknown_model_costs: 0 },
  groups: [
    {
      model: "provider/root-model",
      agent_instance_id: "agent-root",
      descendant: false,
      source: "workflows.custom_source",
      totals: root,
    },
  ],
  other_groups: { ...root, model_requests: 1 },
  recent_runs: [],
  other_runs: { ...root, model_requests: 0 },
};

it("translates usage details while keeping model names, unknown sources and observed counts", async () => {
  render(
    <LanguageProvider>
      <LanguageChoice />
      <DebugProvider>
        <UsageDetails usage={usage} />
      </DebugProvider>
    </LanguageProvider>,
  );
  const breakdown = screen.getByText("Token breakdown").closest("details")!;
  fireEvent.click(screen.getByText("Token breakdown"));
  fireEvent.click(screen.getByText("Agents and sources"));
  expect(breakdown.open).toBe(true);
  expect(screen.getByText("3 requests")).toBeTruthy();
  expect(screen.getByText("Root · workflows.custom_source")).toBeTruthy();
  expect(screen.getByText("1 response has no recorded price.")).toBeTruthy();
  expect(screen.getByText("Non-model providers · 1 receipt")).toBeTruthy();
  await language("简体中文");
  expect(breakdown.open).toBe(true);
  expect(screen.getByText("token 明细")).toBeTruthy();
  expect(screen.getByText("3 次请求")).toBeTruthy();
  expect(screen.getByText("主智能体 · workflows.custom_source")).toBeTruthy();
  expect(screen.getByText("provider/root-model")).toBeTruthy();
  expect(screen.getByText("其他模型")).toBeTruthy();
  expect(screen.getByText("1 次响应没有记录价格。")).toBeTruthy();
  expect(screen.getByText("非模型提供商 · 1 条收据")).toBeTruthy();
  expect(screen.getByText("USD").nextElementSibling!.textContent).toBe(
    "0.5000",
  );
  fireEvent.click(screen.getByRole("button", { name: "子智能体" }));
  await language("English");
  expect(
    screen
      .getByRole("button", { name: "Subagents" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  expect(screen.getByText("Token breakdown")).toBeTruthy();
});

it("keeps legacy overflow attribution when model scopes are absent", () => {
  render(
    <DebugProvider>
      <UsageDetails
        usage={{
          ...usage,
          groups: [
            {
              model: "Other models",
              agent_instance_id: "root-agent",
              descendant: false,
              source: "agent",
              totals: root,
            },
          ],
        }}
      />
    </DebugProvider>,
  );
  expect(screen.getByText("Other models")).toBeTruthy();
  expect(screen.getByText("Root · Agent requests")).toBeTruthy();
});

it("localizes the current overflow model while preserving scoped attribution", async () => {
  const scopedUsage: Schema<"ThreadUsageView"> = {
    ...usage,
    model_scopes: [
      { name: "Other models", root, descendants: root, combined: root },
    ],
    groups: [
      {
        model: "Other models",
        agent_instance_id: "root-agent",
        descendant: false,
        source: "agent",
        totals: root,
      },
      {
        model: "Other models",
        agent_instance_id: "child-agent",
        descendant: true,
        source: "files.media_understanding",
        totals: root,
      },
    ],
  };
  render(
    <LanguageProvider>
      <LanguageChoice />
      <DebugProvider>
        <UsageDetails usage={scopedUsage} />
      </DebugProvider>
    </LanguageProvider>,
  );
  const row = screen.getByText("Other models").closest("details")!;
  fireEvent.click(screen.getByText("Other models"));
  fireEvent.click(screen.getByText("Agents and sources"));
  expect(screen.getByText("Root · Agent requests")).toBeTruthy();
  expect(
    screen.getByText("Subagent · Media understanding · view"),
  ).toBeTruthy();
  await language("简体中文");
  expect(screen.getByText("其他模型").closest("details")).toBe(row);
  expect(row.open).toBe(true);
  expect(screen.getByText("主智能体 · 智能体请求")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "子智能体" }));
  expect(screen.queryByText("主智能体 · 智能体请求")).toBeNull();
  expect(screen.getByText("子智能体 · 媒体理解 · 查看")).toBeTruthy();
});

it("translates the status metrics and context help without replaying usage requests", async () => {
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  queries.setQueryData(["thread", "one", "usage"], usage);
  queries.setQueryData(["thread", "one", "context-usage"], {
    thread_id: "one",
    context_window: 100000,
    latest_request_tokens: 25000,
  });
  queries.setQueryData(["thread", "one", "configuration"], {
    capture_source: "active_operation",
    receipt_id: "current",
    captured: { agent: { fast: "on" } },
  });
  queries.setQueryData(["thread", "one", "operation", "current"], {
    receipt: {
      receipt_id: "current",
      thread_id: "one",
      submitted_at: "2026-01-01T00:00:00Z",
    },
    status: "completed",
    completed_at: "2026-01-01T00:00:02Z",
  });
  const get = vi.fn(async () => ({ data: {} }));
  const transport = { client: { GET: get } } as unknown as Transport;
  render(
    <QueryClientProvider client={queries}>
      <TransportContext value={transport}>
        <LanguageProvider>
          <DebugProvider>
            <ComposerStatus threadId="one" receipt="current" />
          </DebugProvider>
        </LanguageProvider>
      </TransportContext>
    </QueryClientProvider>,
  );
  const reads = get.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "Context details" }));
  expect(screen.getByRole("heading", { name: "Context usage" })).toBeTruthy();
  const meter = screen.getByRole("meter");
  expect(meter.getAttribute("aria-valuetext")).toBe("25,000 of 100,000 tokens");
  expect(screen.getByText("tokens used")).toBeTruthy();
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(screen.getByRole("meter")).toBe(meter);
  expect(screen.getByRole("heading", { name: "上下文用量" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "上下文详情" })).toBeTruthy();
  expect(screen.getByText("已用 tokens")).toBeTruthy();
  expect(meter.getAttribute("aria-valuetext")).toBe(
    "已用 25,000 tokens，共 100,000",
  );
  expect(screen.getByText("缓存命中率")).toBeTruthy();
  expect(get.mock.calls.length).toBe(reads);
  fireEvent.keyDown(window, {
    key: "d",
    code: "KeyD",
    ctrlKey: true,
    altKey: true,
  });
  expect(screen.getByText("速度").nextElementSibling?.textContent).toBe("快速");
  expect(screen.getByText("模型费用 · 美元")).toBeTruthy();
  expect(screen.getByText("耗时")).toBeTruthy();
  queries.clear();
});
