"""
UI module — all presentation logic lives here.

Uses `rich` for:
- Pretty tables for query results
- Syntax-highlighted SQL
- Spinner / progress indicator
- Markdown rendering
- Color-coded panels

agent.py handles logic; ui.py handles display.
"""
import json
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.text import Text
from rich.align import Align


console = Console()


def print_welcome():
    title = Text("🤖 DATAGEN", style="bold cyan", justify="center")
    subtitle = Text("Talk to any SQLite database in natural language", style="dim", justify="center")

    console.print(Panel(
        Align.center(Text.assemble(title, "\n", subtitle)),
        border_style="cyan",
        padding=(1, 2),
    ))

    help_text = Text()
    help_text.append("💡 Example questions:\n", style="bold yellow")
    help_text.append("   • What was total revenue in April 2026?\n", style="dim")
    help_text.append("   • Which product sold the most in the Fashion category?\n", style="dim")
    help_text.append("   • Show the top 5 customers by purchase amount.\n", style="dim")
    help_text.append("\n⚙️  Commands:\n", style="bold yellow")
    help_text.append("   /reset   ", style="cyan")
    help_text.append("- start a new conversation\n", style="dim")
    help_text.append("   /stats   ", style="cyan")
    help_text.append("- show token usage\n", style="dim")
    help_text.append("   /logs    ", style="cyan")
    help_text.append("- show log file path\n", style="dim")
    help_text.append("   /quit    ", style="cyan")
    help_text.append("- exit", style="dim")

    console.print(help_text)
    console.print()


def prompt_user() -> str:
    console.print()
    return console.input("[bold green]💬 You:[/bold green] ").strip()


def print_iteration(iteration: int):
    console.print(f"\n[dim]─── Iteration {iteration} ───[/dim]")


def print_tool_call(tool_name: str, arguments: dict):
    if tool_name == "execute_sql":
        sql = arguments.get("sql", "")
        syntax = Syntax(
            sql, "sql",
            theme="monokai",
            line_numbers=False,
            word_wrap=True,
            background_color="default",
        )
        console.print(Panel(
            syntax,
            title=f"[bold]🔧 {tool_name}[/bold]",
            border_style="blue",
            padding=(0, 1),
        ))

    elif tool_name == "get_distinct_values":
        table = arguments.get("table", "")
        column = arguments.get("column", "")
        console.print(
            f"[blue]🔍 [bold]{tool_name}[/bold][/blue] "
            f"→ exploring [yellow]{table}.{column}[/yellow]"
        )

    elif tool_name == "web_search":
        query = arguments.get("query", "")
        console.print(
            f"[cyan]🌐 [bold]{tool_name}[/bold][/cyan] "
            f"→ [yellow]\"{query}\"[/yellow]"
        )

    else:
        args_str = ", ".join(f"{k}={v}" for k, v in arguments.items())
        console.print(f"[blue]🔧 {tool_name}({args_str})[/blue]")


def print_tool_result(result_json: str):
    try:
        result = json.loads(result_json)
    except json.JSONDecodeError:
        console.print("[red]❌ Invalid JSON result[/red]")
        return

    if not result.get("success"):
        error_text = Text()
        error_text.append("❌ Error: ", style="bold red")
        error_text.append(result.get("error", "unknown"), style="red")
        if result.get("hint"):
            error_text.append("\n💡 Hint: ", style="bold yellow")
            error_text.append(result["hint"], style="yellow")
        console.print(Panel(error_text, border_style="red", padding=(0, 1)))
        return

    if "distinct_values" in result:
        values = result["distinct_values"]
        total = result.get("total_distinct", len(values))
        console.print(
            f"[green]✅[/green] Found [bold]{total}[/bold] distinct values: "
            f"[cyan]{values}[/cyan]"
        )
        return

    if "results" in result and "query" in result:
        answer = result.get("answer")
        sources = result.get("results", [])

        web_text = Text()
        if answer:
            web_text.append("🌐 Web summary:\n", style="bold cyan")
            web_text.append(answer[:600], style="default")
            web_text.append("\n\n")
        web_text.append(f"🔗 Sources ({len(sources)}):\n", style="bold")
        for src in sources[:5]:
            web_text.append(f"  • {src.get('title', 'untitled')}\n", style="cyan")
            web_text.append(f"    {src.get('url', '')}\n", style="dim")

        console.print(Panel(
            web_text,
            title="[bold cyan]🌐 web_search[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        ))
        return

    rows = result.get("rows", [])
    row_count = result.get("row_count", 0)

    if row_count == 0:
        console.print("[green]✅[/green] [dim](no rows returned)[/dim]")
        return

    table = _build_results_table(rows, result.get("columns", []))
    console.print(table)

    info_text = f"[green]✅[/green] {row_count} row(s)"
    if result.get("note"):
        info_text += f" — [yellow]{result['note']}[/yellow]"
    console.print(info_text)


def _build_results_table(rows: list[dict], columns: list[str]) -> Table:
    table = Table(
        show_header=True,
        header_style="bold magenta",
        border_style="dim",
        padding=(0, 1),
    )

    if not columns and rows:
        columns = list(rows[0].keys())

    for col in columns:
        is_numeric = any(
            kw in col.lower()
            for kw in ["id", "count", "total", "amount", "price", "qty",
                       "quantity", "sum", "avg"]
        )
        table.add_column(col, justify="right" if is_numeric else "left", overflow="fold")

    DISPLAY_LIMIT = 20
    for row in rows[:DISPLAY_LIMIT]:
        table.add_row(*[_format_cell(row.get(col)) for col in columns])

    if len(rows) > DISPLAY_LIMIT:
        table.add_row(*["..."] * len(columns), style="dim")

    return table


def _format_cell(value: Any) -> str:
    if value is None:
        return "[dim]NULL[/dim]"
    if isinstance(value, int) and abs(value) >= 1000:
        return f"{value:,}".replace(",", ".")
    if isinstance(value, float):
        if abs(value) >= 1000:
            return f"{value:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
        return f"{value:.2f}"
    s = str(value)
    return s[:57] + "..." if len(s) > 60 else s


def print_assistant_answer(content: str):
    console.print()
    console.print(Panel(
        Markdown(content),
        title="[bold cyan]🤖 Agent[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))


def thinking_spinner(text: str = "Thinking..."):
    return console.status(f"[cyan]{text}[/cyan]", spinner="dots")


def print_stats(stats: dict):
    table = Table(
        title="📊 Session Stats",
        show_header=True,
        header_style="bold",
        border_style="dim",
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="yellow")

    table.add_row("Session ID", str(stats.get("session_id") or "n/a"))
    table.add_row("Input tokens", f"{stats['total_input_tokens']:,}")
    table.add_row("Output tokens", f"{stats['total_output_tokens']:,}")
    table.add_row("Total tokens", f"{stats['total_tokens']:,}")
    table.add_row("Messages", f"{stats['messages_in_history']:,}")

    console.print(table)


def info(message: str):
    console.print(f"[cyan]ℹ️  {message}[/cyan]")


def success(message: str):
    console.print(f"[green]✅ {message}[/green]")


def warning(message: str):
    console.print(f"[yellow]⚠️  {message}[/yellow]")


def error(message: str):
    console.print(f"[red]❌ {message}[/red]")


def dim(message: str):
    console.print(f"[dim]{message}[/dim]")


if __name__ == "__main__":
    print_welcome()

    info("Demo mode — testing all UI components")
    console.print()

    print_iteration(1)
    print_tool_call("execute_sql", {
        "sql": """SELECT p.name, p.category, SUM(oi.quantity) as total_sold
FROM order_items oi
JOIN products p ON p.product_id = oi.product_id
JOIN orders o ON o.order_id = oi.order_id
WHERE o.status = 'completed'
GROUP BY p.product_id
ORDER BY total_sold DESC
LIMIT 5"""
    })

    fake_result = json.dumps({
        "success": True,
        "row_count": 5,
        "columns": ["name", "category", "total_sold"],
        "rows": [
            {"name": "Kabel Charger Type-C", "category": "Elektronik", "total_sold": 245},
            {"name": "Masker Wajah Sheet Mask", "category": "Kecantikan", "total_sold": 198},
            {"name": "Keripik Singkong Original", "category": "Makanan", "total_sold": 187},
            {"name": "Hijab Pashmina Voal", "category": "Fashion", "total_sold": 156},
            {"name": "Earphone Bluetooth TWS", "category": "Elektronik", "total_sold": 134},
        ]
    })
    print_tool_result(fake_result)

    print_iteration(2)
    print_tool_call("get_distinct_values", {"table": "orders", "column": "status"})
    print_tool_result(json.dumps({
        "success": True,
        "distinct_values": ["cancelled", "completed", "pending", "shipped"],
        "total_distinct": 4,
    }))

    print_iteration(3)
    print_tool_call("execute_sql", {"sql": "SELECT * FROM customer LIMIT 1"})
    print_tool_result(json.dumps({
        "success": False,
        "error": "SQL Error: no such table: customer",
        "hint": "Wrong table name. Available tables: customers, products, orders, order_items."
    }))

    print_assistant_answer(
        "**Top 5 best-selling products** (completed orders):\n\n"
        "1. Kabel Charger Type-C — 245 sold\n"
        "2. Masker Wajah Sheet Mask — 198 sold\n"
        "3. Keripik Singkong Original — 187 sold\n\n"
        "**Insight:** Electronics and Beauty products dominate the top 5, "
        "with fast-moving consumer goods (low price, high volume) as the clearest pattern."
    )

    print_stats({
        "session_id": "abc123",
        "total_input_tokens": 4521,
        "total_output_tokens": 312,
        "total_tokens": 4833,
        "messages_in_history": 7,
    })
