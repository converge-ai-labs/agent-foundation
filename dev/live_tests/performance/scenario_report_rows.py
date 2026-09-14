"""Only bounded operation reports can produce performance verdicts."""

from dataclasses import dataclass

from .operation_config import SCENARIOS


@dataclass
class Row:
    scenario: str
    configuration: str
    p50: float | None
    p95: float | None
    p99: float | None
    result: str


def verdict(cell, metric):
    if cell["status"] != "verified" or cell["errors"]:
        return "Fail: operation or persistence verification failed"
    budgets = cell["budgets_ms"]
    if not budgets:
        return "Not evaluated: no latency budget; persistence verified"
    eligible = {key: value for key, value in budgets.items() if metric["n"] >= {"p95": 100, "p99": 1000}[key]}
    exceeded = [f"{key.upper()} > {limit:g} ms" for key, limit in eligible.items() if metric[f"{key}_ms"] > limit]
    if exceeded:
        return "Fail: " + ", ".join(exceeded)
    if len(eligible) != len(budgets):
        return "Not evaluated: insufficient samples for configured percentile budgets (P95 >=100, P99 >=1000)"
    return "Pass: " + ", ".join(f"{key.upper()} <= {limit:g} ms" for key, limit in budgets.items())


def rows(document):
    if document.get("schema") != "bounded-operations-v1":
        raise ValueError(
            "Expected bounded-operations-v1; obsolete E2E and barrier-inclusive artifacts are not performance inputs"
        )
    result = []
    environment = document["environment"]
    target = f"PG={environment.get('postgres', 'unused')}; S3={environment.get('s3', 'unused')}"
    for cell in document["cells"]:
        groups = [(outcome, metric) for outcome, metric in cell["summary"].items() if metric["n"]]
        if not groups:
            groups = [("no samples", {"n": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None})]
        for outcome, metric in groups:
            configuration = {
                "concurrency": cell["concurrency"],
                **cell["configuration"],
                "N": metric["n"],
                "peak_inflight": cell["peak_inflight"],
            }
            detail = "; ".join(f"{key}={value}" for key, value in configuration.items())
            status = verdict(cell, metric)
            if document["status"] == "failed" and not status.startswith("Fail"):
                status += "; overall execution failed"
            result.append(
                Row(
                    f"{SCENARIOS[cell['scenario']]} / {outcome}",
                    target + "; " + detail,
                    metric["p50_ms"],
                    metric["p95_ms"],
                    metric["p99_ms"],
                    status,
                )
            )
    return result
