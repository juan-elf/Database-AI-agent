"""
Agent loop — Universal SQL Agent.

System prompt is built in layers: generic instructions + schema + optional domain pack.
Domain packs are loaded from domains/*.md.
Without a domain pack: generic SQL assistant mode.
With a domain pack: domain specialist mode.
"""
import json
import os
import time
from pathlib import Path
from typing import Any

from openai import OpenAI, APIError, RateLimitError, APIConnectionError
from dotenv import load_dotenv

from database import get_schema, get_db_engine, get_db_label
from tools import TOOLS_SCHEMA, call_tool
from logger import ConversationLogger
from web_search import is_available as web_available
from profiler import profile_database, format_profile_for_prompt
from guardrails import harden_system_prompt, check_input
import ui

load_dotenv()

MODEL_NAME = "google/gemma-4-31b-it:free"
MAX_ITERATIONS = 10
MAX_RETRIES = 3
INITIAL_BACKOFF = 2

DOMAINS_DIR = Path("domains")

client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1"
)


def list_available_domains() -> list[str]:
    """List domain pack names available in the domains/ folder."""
    if not DOMAINS_DIR.exists():
        return []
    return sorted([p.stem for p in DOMAINS_DIR.glob("*.md")])


def load_domain_pack(name: str) -> str | None:
    """Load a domain pack by name (without .md extension). Returns None if not found."""
    pack_path = DOMAINS_DIR / f"{name}.md"
    if not pack_path.exists():
        return None
    return pack_path.read_text(encoding="utf-8")


GENERIC_INSTRUCTIONS = """You are a Universal SQL Assistant — help users answer questions about \
their database using natural language.

How you work:
1. Understand the user's question and inspect the database schema
2. Generate the appropriate SELECT SQL query
3. Execute it via the execute_sql tool
4. Format the result into a natural, informative answer

═══════════════════════════════════════════════════════════════
AVAILABLE TOOLS:
═══════════════════════════════════════════════════════════════
- execute_sql: run a SELECT query (primary tool for all data questions)
- get_distinct_values: check unique values in a categorical column
- run_analysis: execute Python/pandas code on SQL results — USE FOR analysis \
that is hard in SQL: correlation, z-score anomaly detection, distribution stats, \
linear trend. NOT for simple aggregation (use execute_sql instead).
- web_search: search external info — use ONLY when (a) data is not in the database, \
OR (b) external context is needed (definitions, benchmarks, industry standards)

═══════════════════════════════════════════════════════════════
SOURCE PRIORITY STRATEGY:
═══════════════════════════════════════════════════════════════
1. Database FIRST for questions about internal data
2. Web search ONLY when:
   a) Data is not in the database (confirmed via SQL), OR
   b) User requests benchmarks, definitions, or external context
3. COMBINE database + web when both are needed
   (e.g. "is our value X normal?")
   When combining, label which data comes from the database vs. the web.

═══════════════════════════════════════════════════════════════
GENERAL RULES:
═══════════════════════════════════════════════════════════════
1. For JOINs, use the foreign keys shown in the schema.
2. If a query errors, READ the 'hint' field in the response and fix it.
   NEVER retry the exact same failing query.
3. If unsure about values in a categorical column, use get_distinct_values first.
4. If the question is ambiguous, ask for clarification before querying.
5. Reply in natural, informative Bahasa Indonesia.
6. Use markdown formatting in answers (bold, lists, tables).
7. Include UNITS on numeric results where relevant (Rp, °C, %, kg, etc.).
"""

_DIALECT_RULES: dict[str, str] = {
    "sqlite": (
        "═══════════════════════════════════════════════════════════════\n"
        "SQL DIALECT: SQLite\n"
        "═══════════════════════════════════════════════════════════════\n"
        "- Monthly grouping: strftime('%Y-%m', date_column)\n"
        "- Dates stored as TEXT in ISO format (YYYY-MM-DD)\n"
        "- String concat: || operator\n"
        "- Window functions supported (LAG, LEAD, ROW_NUMBER, etc.)"
    ),
    "postgres": (
        "═══════════════════════════════════════════════════════════════\n"
        "SQL DIALECT: PostgreSQL (Supabase)\n"
        "═══════════════════════════════════════════════════════════════\n"
        "- Monthly grouping: to_char(date_col, 'YYYY-MM') "
        "or DATE_TRUNC('month', date_col)\n"
        "- Dates stored as DATE or TIMESTAMP\n"
        "- String concat: || operator or CONCAT()\n"
        "- Case-insensitive match: ILIKE\n"
        "- Type casting: value::text, value::integer\n"
        "- Window functions supported (LAG, LEAD, ROW_NUMBER, etc.)"
    ),
}


def build_system_prompt(domain_name: str | None = None) -> str:
    """
    Build the layered system prompt:
    1. Generic instructions + dialect-specific SQL rules
    2. Database schema (auto-detected)
    3. Data profile (row counts, ranges, cardinality — cached)
    4. Domain pack (optional)
    """
    schema = get_schema()
    db_label = get_db_label()
    web_status = "ACTIVE" if web_available() else "INACTIVE"
    dialect = get_db_engine()

    try:
        profile = profile_database()
        profile_text = format_profile_for_prompt(profile)
    except Exception:
        profile_text = "(Data profile unavailable)"

    parts = [
        GENERIC_INSTRUCTIONS,
        _DIALECT_RULES[dialect],
        f"\nWEB SEARCH STATUS: {web_status}\n",
        "═══════════════════════════════════════════════════════════════",
        f"DATABASE: {db_label}",
        "═══════════════════════════════════════════════════════════════",
        schema,
        "\n═══════════════════════════════════════════════════════════════",
        "DATA PROFILE  (cached once — row counts, ranges, cardinality)",
        "═══════════════════════════════════════════════════════════════",
        profile_text,
    ]

    if domain_name:
        domain_content = load_domain_pack(domain_name)
        if domain_content:
            parts.extend([
                "\n═══════════════════════════════════════════════════════════════",
                f"DOMAIN KNOWLEDGE: {domain_name}",
                "═══════════════════════════════════════════════════════════════",
                domain_content,
            ])
        else:
            available = list_available_domains()
            parts.append(
                f"\n[INFO] Domain pack '{domain_name}' not found. "
                f"Available: {available or 'none'}. "
                f"Proceeding without domain knowledge."
            )

    return harden_system_prompt("\n".join(parts))


def _call_api_with_retry(messages: list, tools: list) -> Any:
    """Call the API with exponential backoff retry."""
    last_exception = None
    for attempt in range(MAX_RETRIES):
        try:
            return client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )
        except (RateLimitError, APIConnectionError, APIError) as e:
            last_exception = e
            if attempt < MAX_RETRIES - 1:
                wait = INITIAL_BACKOFF * (2 ** attempt)
                ui.warning(f"{type(e).__name__}, retrying in {wait}s "
                           f"({attempt + 1}/{MAX_RETRIES})...")
                time.sleep(wait)
            else:
                raise
    raise last_exception


class Agent:
    """Universal SQL Agent with optional domain specialization."""

    def __init__(
        self,
        domain: str | None = None,
        verbose: bool = True,
        enable_logging: bool = True,
    ):
        self.verbose = verbose
        self.domain = domain
        self.system_prompt = build_system_prompt(domain)
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.logger = ConversationLogger() if enable_logging else None
        self.total_input_tokens = 0
        self.total_output_tokens = 0

        if self.verbose:
            ui.dim(f"📂 Database: {get_db_label()}")
            ui.dim(f"🎯 Domain pack: {domain or '(none — generic mode)'}")
            if web_available():
                ui.dim("🌐 Web search: active (Tavily)")
            else:
                ui.dim("🌐 Web search: inactive (set TAVILY_API_KEY to enable)")
            if self.logger:
                ui.dim(f"📁 Log: {self.logger.get_log_path()}")

    def get_stats(self) -> dict:
        return {
            "session_id": self.logger.session_id if self.logger else None,
            "domain": self.domain or "(none)",
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "messages_in_history": len(self.messages),
        }

    def chat(self, user_message: str) -> str:
        """Process one user message through the agent loop."""
        self.messages.append({"role": "user", "content": user_message})
        if self.logger:
            self.logger.log_user_message(user_message)

        allow, reason = check_input(user_message)
        if not allow:
            blocked_msg = f"Maaf, pesan tidak dapat diproses: {reason}"
            if self.logger:
                self.logger.log_error("input_blocked", reason)
            return blocked_msg

        for iteration in range(MAX_ITERATIONS):
            if self.verbose:
                ui.print_iteration(iteration + 1)

            try:
                if self.verbose:
                    with ui.thinking_spinner("Agent thinking..."):
                        response = _call_api_with_retry(self.messages, TOOLS_SCHEMA)
                else:
                    response = _call_api_with_retry(self.messages, TOOLS_SCHEMA)
            except Exception as e:
                error_msg = (f"API failed after {MAX_RETRIES} retries: "
                             f"{type(e).__name__}: {e}")
                if self.verbose:
                    ui.error(error_msg)
                if self.logger:
                    self.logger.log_error("api_failure", error_msg)
                return f"⚠️ {error_msg}. Please try again later."

            if response.usage:
                self.total_input_tokens += response.usage.prompt_tokens
                self.total_output_tokens += response.usage.completion_tokens

            assistant_msg = response.choices[0].message

            if assistant_msg.tool_calls:
                self.messages.append({
                    "role": "assistant",
                    "content": assistant_msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in assistant_msg.tool_calls
                    ]
                })

                for tool_call in assistant_msg.tool_calls:
                    tool_name = tool_call.function.name

                    try:
                        tool_args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError as e:
                        error_result = json.dumps({
                            "success": False,
                            "error": f"Invalid tool arguments (not valid JSON): {e}"
                        })
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": error_result
                        })
                        continue

                    if self.verbose:
                        ui.print_tool_call(tool_name, tool_args)

                    result = call_tool(tool_name, tool_args)

                    if self.verbose:
                        ui.print_tool_result(result)

                    if self.logger:
                        self.logger.log_tool_call(tool_name, tool_args, result)

                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result
                    })

                continue

            final_answer = assistant_msg.content
            self.messages.append({"role": "assistant", "content": final_answer})

            if self.logger:
                self.logger.log_assistant_message(
                    final_answer,
                    token_usage={
                        "total_input": self.total_input_tokens,
                        "total_output": self.total_output_tokens,
                    }
                )
            return final_answer

        msg = "⚠️ Max iterations reached. Try a more specific question."
        if self.logger:
            self.logger.log_error("max_iterations", msg)
        return msg

    def reset(self):
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.logger = ConversationLogger() if self.logger else None
