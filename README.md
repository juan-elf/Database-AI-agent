# Universal SQL Agent

[![Tests](https://github.com/juan-elf/Database-AI-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/juan-elf/Database-AI-agent/actions/workflows/tests.yml)

An LLM-powered CLI agent that answers natural language questions about any SQLite database. The agent generates SQL, executes it, optionally searches the web for external context, and replies in clean formatted text.

Built on **MiniMax** (OpenAI-compatible API) with a hybrid DB + web search strategy, SQL self-correction, and per-session JSONL observability logs. Comes with a **Streamlit dashboard** for chat, data exploration, and session analytics.

---

## Features

- **Talk to any SQLite database** — point it at any `.db` file, no schema configuration needed
- **Domain packs** — drop a `.md` file in `domains/` to give the agent specialist knowledge (glossary, query patterns, pitfalls)
- **Hybrid knowledge** — database-first for internal data, Tavily web search for benchmarks, definitions, and external context
- **Self-correcting** — query errors return a `hint` that the agent uses to fix and retry
- **Safe by design** — only `SELECT`/`WITH` allowed; dangerous keywords blacklisted; multi-statement blocked; identifier validation
- **Pretty CLI** — `rich`-based tables, syntax-highlighted SQL, web result panels, spinners, markdown rendering
- **Observability** — every session logged to JSONL
- **Auto-chart generation** — SQL results with numeric/time-series data are automatically visualized as charts in the dashboard chat

---

## Architecture

```
+----------+    user input    +----------+
| main.py  | --------------> | agent.py |
|  (CLI)   | <-------------- |  (loop)  |
+----------+    rich output   +----+-----+
                                   |
                          tool_calls|
                                   v
                    +--------------+---------------+
                    |              |               |
               +----+----+   +----+----+   +-------+------+
               |tools.py |   |tools.py |   | tools.py     |
               |execute_ |   |get_     |   | web_search   |
               |sql      |   |distinct |   |              |
               +---------+   +---------+   +--------------+
                    |                            |
                    v                            v
             +-----------+              +----------------+
             |database.py|              | web_search.py  |
             | + SQLite  |              | (Tavily API)   |
             +-----------+              +----------------+
                    |
                    | log every event
                    v
               +---------+
               |logger.py| --> logs/*.jsonl
               +---------+
```

### File overview

| File | Purpose |
|---|---|
| `main.py` | CLI entry point + interactive chat loop |
| `agent.py` | Agent loop — call API → handle tool calls → loop until final answer |
| `tools.py` | Tool schema & dispatcher (`execute_sql`, `get_distinct_values`, `web_search`) |
| `database.py` | SQLite connection, query validation, error hints, schema introspection |
| `web_search.py` | Tavily API integration for external web search |
| `ui.py` | All presentation logic (rich-based) — panels, tables, spinners, markdown |
| `logger.py` | Per-session JSONL logger |
| `dashboard.py` | Streamlit web dashboard — Chat, DB Explorer, Session History, Analytics |
| `domains/` | Domain pack files (`*.md`) — specialist knowledge injected into the system prompt |
| `data/` | SQLite database files (gitignored) |
| `logs/` | Per-session log files (gitignored) |

---

## Setup

### 1. Prerequisites

- Python 3.11+
- MiniMax API key — [minimax.io](https://www.minimax.io/)
- *(Optional)* Tavily API key for web search — [tavily.com](https://tavily.com) (free tier: 1,000 searches/month)

### 2. Install dependencies

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Configure `.env`

```env
MINIMAX_API_KEY=sk-your-key-here
TAVILY_API_KEY=tvly-xxxxx        # optional — enables web search
```

---

## Dashboard

A Streamlit web dashboard for visual interaction with the agent.

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run dashboard.py
# Opens at http://localhost:8501
```

### Dashboard tabs

| Tab | Description |
|---|---|
| 📊 **Dashboard** | KPI overview, SOH/capacity charts, session table |
| 💬 **Chat** | Talk to the agent in-browser; SQL results auto-visualized as charts |
| 🗄️ **DB Explorer** | Schema browser, data preview, quick charts |
| 📋 **Riwayat Sesi** | Browse all session logs with full Q&A timeline |
| 📈 **Analytics** | Aggregate stats — tool usage, token breakdown, session comparison |

### Auto-chart generation

When the agent executes a SQL query in the Chat tab, the dashboard automatically detects the result shape and renders the most appropriate chart:

| Result shape | Chart type |
|---|---|
| Numeric column(s) + time/cycle axis | Line chart |
| Categorical column + numeric | Bar chart |
| Two numeric columns | Scatter plot |
| Single value or < 2 rows | No chart (not worth visualizing) |

---

## Usage

```powershell
# Generic mode — no domain specialization
python main.py --db data/my_database.db

# With a domain pack
python main.py --db data/battery.db --domain battery
python main.py --db data/shop.db --domain ecommerce

# List available domain packs
python main.py --list-domains

# Disable session logging
python main.py --db data/my_database.db --no-log
```

### CLI commands (while running)

| Command | Action |
|---|---|
| `/reset` | Start a new conversation (clears history) |
| `/stats` | Show token usage and message count |
| `/logs` | Show the current session log file path |
| `/help` | Show available commands |
| `/quit` or `/exit` | Exit |

---

## Domain Packs

A domain pack is a Markdown file in the `domains/` folder. When loaded, its content is appended to the system prompt, giving the agent specialist knowledge for a specific dataset type.

**Included domain packs:**

| Pack | Use for |
|---|---|
| `battery` | Li-ion battery degradation datasets (SOH, RUL, cycle analysis) |
| `ecommerce` | E-commerce / retail / marketplace datasets (revenue, customers, products) |

**Adding a new domain pack:**

1. Create `domains/your_domain.md`
2. Include: glossary, column naming conventions, query patterns, common pitfalls
3. Run with `--domain your_domain`

---

## Tool Strategy

The agent has three tools and uses them based on the question type:

| Question type | Strategy |
|---|---|
| Data in the database | `execute_sql` first |
| Unknown column values / categories | `get_distinct_values` before filtering |
| External benchmarks, definitions, typical values | `web_search` |
| "Is our data normal?" | Both: SQL → web → combine and label sources |

Web search is **optional** — if `TAVILY_API_KEY` is not set, the agent operates in DB-only mode.

---

## Query Safety

The database tool is **read-only** with defense-in-depth:

1. **Whitelist** — only `SELECT` / `WITH` is allowed
2. **Blacklist** — `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `CREATE`, `REPLACE`, `ATTACH`, `DETACH`, `PRAGMA`, `VACUUM` are rejected
3. **No multi-statement** — `SELECT 1; SELECT 2` is rejected
4. **Identifier validation** — table/column names in `get_distinct_values` are validated against `^[a-zA-Z_][a-zA-Z0-9_]*$`

---

## Observability

Every session is logged to `logs/session_<timestamp>_<id>.jsonl` (JSON Lines format).

Logged events: `session_start`, `user_message`, `tool_call`, `assistant_message`, `error`

Quick analysis with PowerShell:

```powershell
Get-Content logs\session_*.jsonl | ConvertFrom-Json | Where-Object event -eq 'tool_call'
```

---

## Eval Harness

Automated accuracy evaluation for the agent. Runs natural-language test cases, captures the SQL results the agent produces, and compares them to ground-truth results from a reference SQL query.

```powershell
# Validate all expected SQL (no API calls)
python eval/run_eval.py --db data/battery.db --domain battery --dry-run

# Full eval run
python eval/run_eval.py --db data/battery.db --domain battery

# Filter by tag
python eval/run_eval.py --db data/battery.db --domain battery --tags eol,filter

# Custom numeric tolerance (default 1%)
python eval/run_eval.py --db data/battery.db --domain battery --tolerance 0.02

# Save results to JSON
python eval/run_eval.py --db data/battery.db --domain battery --output results.json
```

### How it works

1. Each test case has a `question` (sent to the agent) and an `expected_sql` (ground-truth reference)
2. The harness executes `expected_sql` directly to get the expected rows
3. The agent is run with the question; its SQL tool calls are intercepted
4. The agent's last successful SQL result is compared to the expected rows by **value** (column names are ignored, floats are compared within tolerance)
5. Results are reported per case and aggregated by tag

### Metrics reported

| Metric | Description |
|---|---|
| Pass rate | % cases where agent result matches expected |
| Accuracy by tag | Pass rate broken down by query category |
| SQL attempts | How many SQL calls the agent made per case (retries visible) |

### Test cases

| File | Domain | Cases | Tags covered |
|---|---|---|---|
| `eval/cases/battery.jsonl` | Li-ion battery degradation | 21 | count, filter, eol, aggregation, group-by, ranking, having, window-function, computed, percentage |
| `eval/cases/ecommerce.jsonl` | E-commerce / retail | 17 | count, filter, join, aggregation, revenue, ranking, time-series, having, subquery, customer-behavior |

To add a new case, append a line to the relevant `.jsonl` file:

```jsonl
{"id": "bat_022", "question": "...", "expected_sql": "SELECT ...", "tags": ["filter"]}
```

Add `"order_matters": true` when the test explicitly checks row ordering.

### Latest eval results

> Measured on `data/demo.db`. Run `python eval/run_eval.py --db data/demo.db --domain battery --output eval/results_battery.json` to refresh.

| Dataset | Model | Cases | Pass | Accuracy | Date |
|---------|-------|-------|------|----------|------|
| battery (demo.db) | MiniMax-M2.7 | 21 | 14 | **66.7%** | 2026-06-10 |
| ecommerce | MiniMax-M2.7 | 17 | — | *requires ecommerce.db* | — |

**Accuracy by tag (battery baseline):**

| Tag | Accuracy | Tag | Accuracy |
|-----|----------|-----|----------|
| count | 100% | group-by | 83% |
| subquery | 100% | aggregation | 73% |
| having | 100% | filter | 50% |
| temperature | 100% | eol | 33% |
| percentage | 100% | ranking | 0% |
| degradation | 100% | window-function | 0% |

**Failure patterns identified:**

| Pattern | Cases | Description |
|---------|-------|-------------|
| Extra columns | bat_003, bat_004, bat_005, bat_013 | Agent adds unrequested context columns (battery_id, soh, cycle) |
| Missing LIMIT | bat_008, bat_017 | Ranking queries return all rows instead of top-1 |
| Sign/polarity | bat_020 | "Largest drop" returned as positive vs expected negative |

---

## Agent Configuration

Key constants in `agent.py`:

| Constant | Default | Description |
|---|---|---|
| `MODEL_NAME` | `MiniMax-M2.7` | Model in use |
| `MAX_ITERATIONS` | `10` | Max agent loop iterations per question |
| `MAX_RETRIES` | `3` | API retries on transient errors |
| `INITIAL_BACKOFF` | `2` | Initial backoff in seconds (exponential: 2s, 4s, 8s) |

---

## Troubleshooting

**`MINIMAX_API_KEY not found`**  
→ Make sure `.env` exists in the project root and contains `MINIMAX_API_KEY=...`

**`Database not found`**  
→ Check the path passed to `--db`. Run `python main.py --help` for usage.

**`TAVILY_API_KEY not found`**  
→ Add `TAVILY_API_KEY=tvly-xxxxx` to `.env`. Sign up free at [tavily.com](https://tavily.com). The agent works without it in DB-only mode.

**`APIError` / `RateLimitError` repeated**  
→ Check your API credit and internet connection. The agent retries 3× with exponential backoff.

**Agent gives wrong queries or gets stuck in a loop**  
→ Open the latest log in `logs/`, check which `tool_call` errored and what hint was returned. Try `/reset` and rephrase the question.

---

## Known Limitations

| Area | Limitation | Status |
|------|-----------|--------|
| **Database** | SQLite-only. No PostgreSQL/MySQL support yet. | Planned (Fase 2) |
| **Context growth** | Conversation history is unbounded — very long sessions will eventually hit the model's context limit. | Planned fix |
| **Schema injection** | Full schema is injected into every system prompt as raw text. Not scalable past ~20 tables. | Planned: replace with auto-profiling summary |
| **SQL dialect** | Prompt and domain packs are tuned for SQLite syntax (`strftime`, `sqlite_master`). Porting to Postgres requires prompt edits. | Tracked in roadmap |
| **Write operations** | Read-only by design (SQLite `mode=ro` + keyword whitelist). No INSERT/UPDATE path. | Intentional — see roadmap for safe write architecture |
| **Multi-tenancy** | Single database per session. No row-level security or multi-user isolation. | Out of scope for v1 |

---

## Folder Structure

```
universal-sql-agent/
├── .env                    # API keys (gitignored)
├── .gitignore
├── README.md
├── requirements.txt
├── main.py                 # entry point
├── agent.py                # agent loop
├── tools.py                # tool definitions and dispatcher
├── database.py             # SQLite ops + validation
├── web_search.py           # Tavily web search integration
├── ui.py                   # rich-based presentation
├── logger.py               # JSONL session logger
├── dashboard.py            # Streamlit web dashboard
├── domains/
│   ├── battery.md          # domain pack: Li-ion battery research
│   └── ecommerce.md        # domain pack: e-commerce / retail
├── data/
│   ├── demo.db             # bundled demo database (committed)
│   └── *.db                # other databases (gitignored)
└── logs/
    └── session_*.jsonl     # session logs (gitignored)
```

---

## License

Not yet specified. Learning project.
