# Universal SQL Agent

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
│   └── *.db                # SQLite databases (gitignored)
└── logs/
    └── session_*.jsonl     # session logs (gitignored)
```

---

## License

Not yet specified. Learning project.
