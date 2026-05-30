"""
Universal SQL Agent — CLI entry point.

Usage:
    python main.py --db path/to/database.db
    python main.py --db path/to/database.db --domain battery
    python main.py --db path/to/database.db --list-domains

Examples:
    python main.py --db data/battery.db --domain battery
    python main.py --db data/ecommerce.db --domain ecommerce
    python main.py --db data/my_data.db   # generic mode, no domain
"""
import argparse
import sys
from pathlib import Path

import database
from agent import Agent, list_available_domains, load_domain_pack
import ui


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="universal-sql-agent",
        description="Talk to any SQLite database in natural language.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --db data/battery.db --domain battery
  python main.py --db data/shop.db --domain ecommerce
  python main.py --db data/anything.db
  python main.py --list-domains
        """.strip()
    )
    parser.add_argument("--db", type=str, help="Path to the SQLite database file (.db)")
    parser.add_argument(
        "--domain", type=str, default=None,
        help="Domain pack name (file in domains/ folder, without .md). Optional."
    )
    parser.add_argument(
        "--list-domains", action="store_true",
        help="Show available domain packs and exit."
    )
    parser.add_argument(
        "--no-log", action="store_true",
        help="Disable session logging."
    )
    return parser.parse_args()


def cmd_list_domains():
    """Print all available domain packs."""
    domains = list_available_domains()
    if not domains:
        ui.warning("No domain packs found in domains/ folder.")
        ui.dim("   Create domains/NAME.md to add a domain pack.")
        return

    ui.info(f"Available domain packs ({len(domains)}):")
    for d in domains:
        content = load_domain_pack(d) or ""
        first_line = content.strip().split("\n", 1)[0][:80] if content else ""
        ui.console.print(f"  [cyan]• {d}[/cyan]  [dim]{first_line}[/dim]")


def interactive_loop(agent: Agent):
    """Interactive CLI loop with command handling."""
    while True:
        try:
            user_input = ui.prompt_user()
        except (KeyboardInterrupt, EOFError):
            ui.console.print()
            _farewell(agent)
            break

        if not user_input:
            continue

        cmd = user_input.lower()

        if cmd in ("/quit", "/exit", "quit", "exit"):
            _farewell(agent)
            break

        if cmd == "/reset":
            agent.reset()
            ui.success("Conversation reset.")
            continue

        if cmd == "/stats":
            ui.print_stats(agent.get_stats())
            continue

        if cmd == "/logs":
            if agent.logger:
                ui.info(f"Log: {agent.logger.get_log_path()}")
            else:
                ui.warning("Logging is disabled.")
            continue

        if cmd == "/help":
            _print_help()
            continue

        try:
            answer = agent.chat(user_input)
            ui.print_assistant_answer(answer)
        except KeyboardInterrupt:
            ui.warning("Cancelled by user.")
        except Exception as e:
            ui.error(f"{type(e).__name__}: {e}")
            ui.dim("Try again or type /reset.")


def _farewell(agent: Agent):
    ui.console.print()
    ui.info("Goodbye! 👋")
    ui.print_stats(agent.get_stats())


def _print_help():
    ui.console.print()
    ui.info("Available commands:")
    ui.console.print("  [cyan]/reset[/cyan]   - start a new conversation")
    ui.console.print("  [cyan]/stats[/cyan]   - show token usage")
    ui.console.print("  [cyan]/logs[/cyan]    - show log file path")
    ui.console.print("  [cyan]/help[/cyan]    - show commands")
    ui.console.print("  [cyan]/quit[/cyan]    - exit")


def main():
    args = parse_args()

    if args.list_domains:
        cmd_list_domains()
        sys.exit(0)

    if not args.db:
        ui.error("--db is required. Example: python main.py --db data/my.db")
        ui.dim("   Or use --list-domains to see available domain packs.")
        sys.exit(1)

    db_path = Path(args.db)
    if not db_path.exists():
        ui.error(f"Database not found: {db_path}")
        sys.exit(1)

    try:
        database.set_database(db_path)
    except Exception as e:
        ui.error(f"Failed to set database: {e}")
        sys.exit(1)

    if args.domain:
        if load_domain_pack(args.domain) is None:
            available = list_available_domains()
            ui.warning(f"Domain pack '{args.domain}' not found.")
            if available:
                ui.dim(f"   Available: {', '.join(available)}")
            else:
                ui.dim("   The domains/ folder is empty.")
            ui.dim("   Continuing without a domain pack (generic mode).")
            args.domain = None

    ui.print_welcome()

    try:
        agent = Agent(
            domain=args.domain,
            verbose=True,
            enable_logging=not args.no_log,
        )
    except Exception as e:
        ui.error(f"Failed to initialize agent: {type(e).__name__}: {e}")
        sys.exit(1)

    interactive_loop(agent)


if __name__ == "__main__":
    main()
