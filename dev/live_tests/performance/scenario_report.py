"""Render explicitly selected native benchmark reports as one scenario-first table."""

import argparse
import csv
import json
from pathlib import Path

from .scenario_report_rows import rows

HEADERS = ("Scenario", "Test configuration", "P50 (ms)", "P95 (ms)", "P99 (ms)", "Performance result")


def cells(row):
    return [
        row.scenario,
        row.configuration,
        *("—" if value is None else f"{value:.2f}" for value in (row.p50, row.p95, row.p99)),
        row.result,
    ]


def markdown(report_rows):
    def line(values):
        return "| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in values) + " |"

    return "\n".join(
        [
            "# Performance by scenario",
            "",
            "Each row is one operation, outcome and configuration. N is the measured sample count, not concurrency. "
            "Durations are milliseconds; raw reports retain their original percentile method. "
            "Pass requires an existing performance gate. Functional-only or undersampled results are not evaluated.",
            "",
            line(HEADERS),
            "|---|---|---:|---:|---:|---|",
            *(line(cells(row)) for row in report_rows),
            "",
        ]
    )


def generate(paths, output):
    # Resolve/deduplicate explicit paths only; never discover old runs via recursive globs.
    sources = list(dict.fromkeys(Path(path).resolve() for path in paths))
    if not sources:
        raise ValueError("Select at least one native report")
    report_rows = []
    for path in sources:
        measured = rows(json.loads(path.read_text()))
        if not measured:
            raise ValueError(f"Selected report has no measured samples: {path.name}; report not generated")
        report_rows.extend(measured)
    report_rows.sort(key=lambda row: (row.scenario, row.configuration))
    output = Path(output)
    # Parse all inputs before touching any existing output.
    if output.suffix != ".md":
        raise ValueError("Output must end in .md")
    outputs = [output, output.with_suffix(".csv"), output.with_suffix(".sources.json")]
    if any(path.resolve() in sources for path in outputs):
        raise ValueError("Output must not overwrite an input report")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown(report_rows))
    with output.with_suffix(".csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADERS)
        writer.writerows(cells(row) for row in report_rows)
    output.with_suffix(".sources.json").write_text(json.dumps([str(path) for path in sources], indent=2) + "\n")
    return report_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="*", type=Path, help="Native JSON reports from the selected execution")
    parser.add_argument("--input-list", type=Path, help="JSON array of report paths, relative to this list file")
    parser.add_argument("--output", required=True, type=Path, help="Markdown output; also writes CSV and source list")
    args = parser.parse_args()
    paths = args.reports
    if args.input_list:
        entries = json.loads(args.input_list.read_text())
        if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
            parser.error("--input-list must contain a JSON array of paths")
        paths = [*paths, *(args.input_list.parent / entry for entry in entries)]
    try:
        report_rows = generate(paths, args.output)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(f"Scenario report: {args.output}; {len(report_rows)} rows in one table")


if __name__ == "__main__":
    main()
