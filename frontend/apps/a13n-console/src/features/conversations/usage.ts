import { isRecord } from "../../service-client";

/**
 * One usage receipt as the Console presents it. Tokens are the provider's own
 * counters; cost stays a decimal string and is null when nothing was priced,
 * never zero.
 */
export interface StepUsage {
  model: string | null;
  provider: string | null;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  costUsd: string | null;
  pricingStatus: string | null;
}
/**
 * A usage record proves a response was committed; it does not prove the
 * provider reported counters. A record without any token count leaves tokens
 * unknown, and unknown is never rendered as zero.
 */
export function reportsTokens(usage: StepUsage | null | undefined): boolean {
  return (
    !!usage &&
    (usage.inputTokens > 0 ||
      usage.outputTokens > 0 ||
      usage.cacheReadTokens > 0 ||
      usage.cacheWriteTokens > 0)
  );
}

export interface ModelUsageRecord {
  recordId: string;
  responseOrdinal: number;
  delegationId: string | null;
  agentInstanceId: string | null;
  usage: StepUsage;
}
export interface ProviderUsageRecord {
  recordId: string;
  toolCallId: string | null;
  toolId: string | null;
  source: string | null;
  usage: StepUsage;
}
export interface UsageReport {
  reason: string;
  model: ModelUsageRecord[];
  provider: ProviderUsageRecord[];
}

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : 0;
}
/** Decimal values survive JSON as strings; a number is still a reported cost. */
function decimal(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value;
  return typeof value === "number" && Number.isFinite(value)
    ? String(value)
    : null;
}
function text(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function modelRecord(record: Record<string, unknown>): ModelUsageRecord | null {
  const recordId = text(record.record_id);
  if (!recordId) return null;
  const usage = isRecord(record.request_usage) ? record.request_usage : {};
  return {
    recordId,
    responseOrdinal: count(record.response_ordinal),
    delegationId: text(record.delegation_id),
    agentInstanceId: text(record.agent_instance_id),
    usage: {
      model: text(record.model_name),
      provider: text(record.provider_name),
      inputTokens: count(usage.input_tokens),
      outputTokens: count(usage.output_tokens),
      cacheReadTokens: count(usage.cache_read_tokens),
      cacheWriteTokens: count(usage.cache_write_tokens),
      costUsd: decimal(usage.cost),
      pricingStatus: text(record.pricing_status),
    },
  };
}

/** Provider measures are provider-neutral units; only named token units map. */
function measure(record: Record<string, unknown>, unit: string): number {
  const usage = isRecord(record.usage) ? record.usage : {};
  if (!Array.isArray(usage.measures)) return 0;
  const found = usage.measures.find(
    (entry) => isRecord(entry) && entry.unit === unit,
  );
  return isRecord(found) ? count(Number(found.quantity)) : 0;
}

function providerRecord(
  record: Record<string, unknown>,
): ProviderUsageRecord | null {
  const recordId = text(record.record_id);
  if (!recordId) return null;
  const usage = isRecord(record.usage) ? record.usage : {};
  return {
    recordId,
    toolCallId: text(record.tool_call_id),
    toolId: text(record.tool_id),
    source: text(record.source),
    usage: {
      model: null,
      provider: text(usage.provider),
      inputTokens: measure(record, "input_tokens"),
      outputTokens: measure(record, "output_tokens"),
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
      costUsd: decimal(usage.cost),
      pricingStatus: null,
    },
  };
}

/** Read one `usage_report` extension payload into its two record families. */
export function parseUsageReport(
  payload: Record<string, unknown>,
): UsageReport | null {
  if (!Array.isArray(payload.records)) return null;
  const report: UsageReport = {
    reason: text(payload.reason) ?? "",
    model: [],
    provider: [],
  };
  for (const record of payload.records) {
    if (!isRecord(record)) continue;
    if (record.kind === "model") {
      const parsed = modelRecord(record);
      if (parsed) report.model.push(parsed);
    } else if (record.kind === "provider") {
      const parsed = providerRecord(record);
      if (parsed) report.provider.push(parsed);
    }
  }
  return report.model.length || report.provider.length ? report : null;
}
