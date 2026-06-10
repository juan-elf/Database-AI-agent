"""
Eval harness — Universal SQL Agent accuracy evaluation.

Runs a set of natural-language test cases against a live agent, captures the
SQL results the agent produces, and compares them to ground-truth results from
a reference SQL query.

Usage:
    python eval/run_eval.py --db data/battery.db --domain battery
    python eval/run_eval.py --db data/battery.db --domain battery --tolerance 0.02
    python eval/run_eval.py --db data/battery.db --cases eval/cases/battery.jsonl
    python eval/run_eval.py --db data/battery.db --tags eol,filter
    python eval/run_eval.py --db data/battery.db --dry-run

Exit codes:
    0 — all cases passed (or skipped)
    1 — one or more cases failed or errored
"""
import argparse
import json
import sys
import time
from pathlib import Path

# Make project root importable regardless of where this script is invoked.
sys.path.insert(0, str(Path(__file__).parent.parent))

import agent as agent_module
import database as db_module
from database import execute_query

from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console()

CASES_DIR = Path(__file__).parent / "cases"
DEFAULT_TOLERANCE = 0.01  # 1% relative tolerance for numeric comparisons


# ── Test case loading ─────────────────────────────────────────────────────────

def load_cases(path: Path) -> list[dict]:
    cases = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as e:
                console.print(f"[yellow]⚠ Skipping malformed line {lineno} in {path.name}: {e}[/yellow]")
    return cases


def filter_by_tags(cases: list[dict], tags: list[str]) -> list[dict]:
    if not tags:
        return cases
    tag_set = set(tags)
    return [c for c in cases if tag_set.intersection(c.get("tags", []))]


# ── SQL result capture ────────────────────────────────────────────────────────

class _SqlCapture:
    """Collects every execute_sql result during one agent.chat() call."""

    def __init__(self):
        self.executions: list[dict] = []

    def last_success(self) -> dict | None:
        for result in reversed(self.executions):
            if result.get("success"):
                return result
        return None

    @property
    def had_error(self) -> bool:
        return any(not r.get("success") for r in self.executions)

    @property
    def retry_count(self) -> int:
        return max(0, len(self.executions) - 1)


def run_with_capture(the_agent, question: str) -> tuple[str, _SqlCapture]:
    """
    Run agent.chat() while intercepting every call to execute_sql.

    Monkey-patches agent_module.call_tool for the duration of the call, then
    restores the original. Thread-unsafe, but eval runs are sequential.
    """
    capture = _SqlCapture()
    original = agent_module.call_tool

    def _capturing(tool_name: str, arguments: dict) -> str:
        result_json = original(tool_name, arguments)
        if tool_name == "execute_sql":
            capture.executions.append(json.loads(result_json))
        return result_json

    agent_module.call_tool = _capturing
    try:
        answer = the_agent.chat(question)
    finally:
        agent_module.call_tool = original

    return answer, capture


# ── Result comparison ─────────────────────────────────────────────────────────

def _numeric_match(a, b, tolerance: float) -> bool:
    try:
        fa, fb = float(a), float(b)
        if fa == 0 and fb == 0:
            return True
        ref = abs(fa) if fa != 0 else abs(fb)
        return abs(fa - fb) / ref <= tolerance
    except (TypeError, ValueError):
        return False


def _values_equal(expected, actual, tolerance: float) -> bool:
    if _numeric_match(expected, actual, tolerance):
        return True
    return str(expected).strip().lower() == str(actual).strip().lower()


def _row_sort_key(row: dict) -> list[str]:
    """Stable sort key for a result row — stringifies every value."""
    return [str(v) for v in row.values()]


def compare_rows(
    expected: list[dict],
    actual: list[dict],
    tolerance: float,
    order_matters: bool = False,
) -> tuple[bool, str]:
    """
    Compare two row lists by VALUE, ignoring column names.

    When order_matters=False (default), both lists are sorted before
    comparison so that a valid result returned in a different order still
    passes.  Set order_matters=True only when the test case explicitly
    checks that the agent preserves a specific row ordering.

    Returns (passed, reason).
    """
    if not expected and not actual:
        return True, "both empty"

    if len(expected) != len(actual):
        return False, f"row count: expected {len(expected)}, got {len(actual)}"

    if not order_matters:
        expected = sorted(expected, key=_row_sort_key)
        actual   = sorted(actual,   key=_row_sort_key)

    for i, (exp_row, act_row) in enumerate(zip(expected, actual)):
        exp_vals = list(exp_row.values())
        act_vals = list(act_row.values())

        if len(exp_vals) != len(act_vals):
            return False, f"row {i}: column count mismatch ({len(exp_vals)} vs {len(act_vals)})"

        for j, (ev, av) in enumerate(zip(exp_vals, act_vals)):
            if not _values_equal(ev, av, tolerance):
                return False, f"row {i} col {j}: expected {ev!r}, got {av!r}"

    return True, "ok"


# ── Single case runner ────────────────────────────────────────────────────────

def run_case(the_agent, case: dict, tolerance: float) -> dict:
    case_id = case["id"]
    question = case["question"]
    expected_sql = case["expected_sql"]
    tags = case.get("tags", [])

    # Ground-truth: execute expected_sql against the live DB.
    gt = execute_query(expected_sql)
    if not gt["success"]:
        return {
            "id": case_id, "status": "SKIP", "tags": tags,
            "reason": f"expected_sql failed: {gt['error']}",
        }
    expected_rows = gt["rows"]

    # Run the agent.
    t0 = time.perf_counter()
    try:
        _answer, capture = run_with_capture(the_agent, question)
    except Exception as e:
        return {
            "id": case_id, "status": "ERROR", "tags": tags,
            "reason": f"agent exception: {type(e).__name__}: {e}",
            "elapsed": time.perf_counter() - t0,
        }
    elapsed = time.perf_counter() - t0

    # Find the agent's answer.
    last = capture.last_success()
    if last is None:
        return {
            "id": case_id, "status": "FAIL", "tags": tags,
            "reason": "agent ran no successful SQL query",
            "expected_rows": expected_rows,
            "agent_rows": None,
            "sql_attempts": len(capture.executions),
            "elapsed": elapsed,
        }

    agent_rows = last["rows"]
    order_matters = case.get("order_matters", False)
    passed, reason = compare_rows(expected_rows, agent_rows, tolerance, order_matters)

    return {
        "id": case_id,
        "status": "PASS" if passed else "FAIL",
        "tags": tags,
        "reason": reason,
        "expected_rows": expected_rows,
        "agent_rows": agent_rows,
        "sql_attempts": len(capture.executions),
        "retries": capture.retry_count,
        "elapsed": elapsed,
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

STATUS_STYLE = {
    "PASS":  "bold green",
    "FAIL":  "bold red",
    "ERROR": "bold magenta",
    "SKIP":  "dim yellow",
}


def print_results_table(results: list[dict], tolerance: float, elapsed_total: float):
    table = Table(
        title=f"Eval Results  (tolerance={tolerance*100:.1f}%  |  {elapsed_total:.1f}s total)",
        show_header=True,
        header_style="bold",
        border_style="dim",
    )
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", justify="center", no_wrap=True)
    table.add_column("Tags", style="dim")
    table.add_column("Attempts", justify="right")
    table.add_column("Time (s)", justify="right")
    table.add_column("Reason", overflow="fold")

    for r in results:
        status = r["status"]
        style = STATUS_STYLE.get(status, "")
        reason = r.get("reason", "")

        if status == "FAIL":
            exp = r.get("expected_rows")
            got = r.get("agent_rows")
            if exp is not None:
                exp_preview = str(exp)[:60]
                got_preview = str(got)[:60] if got is not None else "None"
                reason = f"{reason}\n  expected: {exp_preview}\n  got:      {got_preview}"

        table.add_row(
            r["id"],
            Text(status, style=style),
            ", ".join(r.get("tags", [])),
            str(r.get("sql_attempts", "-")),
            f"{r.get('elapsed', 0):.1f}",
            reason,
        )

    console.print(table)


def print_summary(results: list[dict]):
    total  = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    errors = sum(1 for r in results if r["status"] == "ERROR")
    skipped= sum(1 for r in results if r["status"] == "SKIP")

    accuracy = (passed / (total - skipped) * 100) if (total - skipped) > 0 else 0.0

    console.print()
    summary = Table.grid(padding=(0, 2))
    summary.add_row(
        Text("PASS",  style="bold green"),  Text(str(passed),  style="green"),
        Text("FAIL",  style="bold red"),    Text(str(failed),  style="red"),
        Text("ERROR", style="bold magenta"),Text(str(errors),  style="magenta"),
        Text("SKIP",  style="dim yellow"),  Text(str(skipped), style="dim"),
        Text("Accuracy", style="bold"),     Text(f"{accuracy:.1f}%", style="bold cyan"),
    )
    console.print(summary)

    # Tag-level breakdown
    tag_stats: dict[str, dict] = {}
    for r in results:
        if r["status"] == "SKIP":
            continue
        for tag in r.get("tags", []):
            s = tag_stats.setdefault(tag, {"pass": 0, "total": 0})
            s["total"] += 1
            if r["status"] == "PASS":
                s["pass"] += 1

    if tag_stats:
        console.print()
        tag_table = Table(title="Accuracy by Tag", border_style="dim", header_style="bold")
        tag_table.add_column("Tag", style="cyan")
        tag_table.add_column("Pass", justify="right")
        tag_table.add_column("Total", justify="right")
        tag_table.add_column("Accuracy", justify="right")

        for tag, s in sorted(tag_stats.items(), key=lambda x: -x[1]["pass"] / x[1]["total"]):
            pct = s["pass"] / s["total"] * 100
            style = "green" if pct == 100 else ("yellow" if pct >= 50 else "red")
            tag_table.add_row(tag, str(s["pass"]), str(s["total"]), Text(f"{pct:.0f}%", style=style))

        console.print(tag_table)

    return passed, failed, errors


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_eval",
        description="Evaluate Universal SQL Agent accuracy against a test case set.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python eval/run_eval.py --db data/battery.db --domain battery
  python eval/run_eval.py --db data/battery.db --cases eval/cases/battery.jsonl --tolerance 0.02
  python eval/run_eval.py --db data/battery.db --tags eol,filter
  python eval/run_eval.py --db data/battery.db --dry-run
        """.strip(),
    )
    parser.add_argument("--db", required=True, help="Path to SQLite database file")
    parser.add_argument("--domain", default=None, help="Domain pack name (optional)")
    parser.add_argument(
        "--cases", default=None,
        help="Path to a .jsonl test case file. Defaults to eval/cases/<domain>.jsonl"
    )
    parser.add_argument(
        "--tolerance", type=float, default=DEFAULT_TOLERANCE,
        help=f"Relative tolerance for numeric comparisons (default {DEFAULT_TOLERANCE})"
    )
    parser.add_argument(
        "--tags", default=None,
        help="Comma-separated list of tags to filter cases (e.g. eol,filter)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate test cases and DB connection without calling the API"
    )
    parser.add_argument(
        "--output", default=None,
        help="Write full results as JSON to this file path"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Resolve cases file
    if args.cases:
        cases_path = Path(args.cases)
    elif args.domain:
        cases_path = CASES_DIR / f"{args.domain}.jsonl"
    else:
        console.print("[red]❌ Provide --domain or --cases.[/red]")
        sys.exit(1)

    if not cases_path.exists():
        console.print(f"[red]❌ Cases file not found: {cases_path}[/red]")
        sys.exit(1)

    # Setup database
    db_path = Path(args.db)
    if not db_path.exists():
        console.print(f"[red]❌ Database not found: {db_path}[/red]")
        sys.exit(1)

    db_module.set_database(db_path)

    # Load and filter cases
    cases = load_cases(cases_path)
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",")]
        cases = filter_by_tags(cases, tags)

    if not cases:
        console.print("[yellow]⚠ No test cases to run.[/yellow]")
        sys.exit(0)

    console.print(f"\n[bold]Running {len(cases)} case(s)[/bold] from [cyan]{cases_path.name}[/cyan]"
                  f"  db=[cyan]{db_path.name}[/cyan]"
                  f"  domain=[cyan]{args.domain or 'none'}[/cyan]\n")

    # Dry run: validate expected_sql and exit
    if args.dry_run:
        console.print("[dim]Dry run — validating expected SQL only...[/dim]\n")
        all_ok = True
        for case in cases:
            result = execute_query(case["expected_sql"])
            if result["success"]:
                console.print(f"  [green]OK  [/green] [cyan]{case['id']}[/cyan] — {result['row_count']} row(s)")
            else:
                console.print(f"  [red]FAIL[/red] [cyan]{case['id']}[/cyan] — {result['error']}")
                all_ok = False
        console.print()
        sys.exit(0 if all_ok else 1)

    # Initialize agent (silent mode for eval)
    from agent import Agent
    the_agent = Agent(domain=args.domain, verbose=False, enable_logging=False)

    # Run all cases
    results = []
    t_start = time.perf_counter()

    for i, case in enumerate(cases, 1):
        console.print(f"[dim]  [{i:2d}/{len(cases)}] {case['id']} ...[/dim]", end="")
        result = run_case(the_agent, case, args.tolerance)
        status = result["status"]
        style = STATUS_STYLE.get(status, "")
        console.print(f"\r  [{i:2d}/{len(cases)}] {case['id']}  [{style}]{status}[/{style}]")
        results.append(result)

    elapsed_total = time.perf_counter() - t_start

    # Report
    console.print()
    print_results_table(results, args.tolerance, elapsed_total)
    passed, failed, errors = print_summary(results)

    # Optional JSON output
    if args.output:
        out_path = Path(args.output)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump({
                "db": str(db_path),
                "domain": args.domain,
                "tolerance": args.tolerance,
                "elapsed_total": elapsed_total,
                "results": results,
            }, f, ensure_ascii=False, indent=2, default=str)
        console.print(f"\n[dim]Results written to {out_path}[/dim]")

    sys.exit(0 if failed == 0 and errors == 0 else 1)


if __name__ == "__main__":
    main()
