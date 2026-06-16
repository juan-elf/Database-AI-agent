"""
Insight Report — autonomous multi-step analysis pipeline.

Pipeline:
  1. profile_database()           → schema + stats context
  2. LLM plan (JSON)              → N analytical questions + SQL queries
  3. execute_query() per question → raw rows
  4. _detect_anomaly()            → z-score check on numeric columns
  5. LLM synthesize               → executive summary + findings + recommendations
"""
import json
import re
import statistics
from datetime import datetime
from typing import Any, Callable

from database import execute_query, get_db_label, get_db_engine, get_schema
from profiler import profile_database, format_profile_for_prompt
from agent import client, MODEL_NAME

N_QUESTIONS = 6

# ── Prompts ───────────────────────────────────────────────────────────────────

_PLAN_SYSTEM = (
    "You are a senior data analyst. Given a database profile and schema, "
    "generate analytical questions and the SQL queries that answer them. "
    "Reply with valid JSON only — no prose, no markdown fences."
)

_PLAN_USER = """\
Database: {db_label}
SQL dialect: {dialect}

Schema:
{schema}

Data profile:
{profile}

Generate exactly {n} diverse analytical questions covering:
- overall statistics and data quality
- trends or patterns over time / cycles (if applicable)
- comparisons across groups or categories
- top/bottom rankings
- potential anomalies or outliers
- derived/computed metrics (ratios, rates, deltas)

Each question must have a SQL query valid for the given dialect.

JSON format (no other text):
{{
  "questions": [
    {{"question": "...", "sql": "SELECT ...", "type": "summary|trend|comparison|ranking|anomaly|computed"}}
  ]
}}"""

_SYNTHESIZE_SYSTEM = (
    "You are a senior data analyst writing an executive insight report "
    "in Bahasa Indonesia. Use markdown. Be specific — reference actual numbers."
)

_SYNTHESIZE_USER = """\
Database: {db_label}

Findings from analysis:
{findings_text}

Write a report using exactly these section headers:

## Ringkasan Eksekutif
(2-3 sentences highlighting the most important findings)

## Temuan Utama
(one sub-section per finding with specific numbers; mark anomalies clearly)

## Rekomendasi
(exactly 3 actionable bullet points)\
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def _call_llm(system: str, user: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, stripping optional markdown code fences."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    # Find outermost { ... } in case of trailing text
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


def _summarize_rows(rows: list[dict], max_rows: int = 8) -> str:
    """Compact text table for inclusion in the synthesis prompt."""
    if not rows:
        return "(no data returned)"
    header = " | ".join(str(k) for k in rows[0])
    sep    = "-" * max(len(header), 20)
    lines  = [header, sep]
    for row in rows[:max_rows]:
        lines.append(" | ".join(str(v) for v in row.values()))
    if len(rows) > max_rows:
        lines.append(f"... ({len(rows) - max_rows} more rows)")
    return "\n".join(lines)


def _detect_anomaly(rows: list[dict]) -> bool:
    """Return True if any numeric column contains a z-score outlier (> 2.5 σ)."""
    if len(rows) < 4:
        return False
    for col in rows[0]:
        vals = [r[col] for r in rows if isinstance(r.get(col), (int, float))]
        if len(vals) < 4:
            continue
        try:
            mean  = statistics.mean(vals)
            stdev = statistics.stdev(vals)
            if stdev == 0:
                continue
            if any(abs(v - mean) / stdev > 2.5 for v in vals):
                return True
        except Exception:
            pass
    return False

# ── Public API ────────────────────────────────────────────────────────────────

def generate_report(
    n_questions: int = N_QUESTIONS,
    on_progress: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """
    Run the full insight report pipeline.

    Args:
        n_questions:  number of analytical questions to generate (default 6)
        on_progress:  optional callback(step, detail) for UI progress updates

    Returns a dict with keys:
        title, db_label, generated_at, narrative (full markdown),
        executive_summary, findings (list of dicts), recommendations, errors
    """
    def progress(step: str, detail: str = "") -> None:
        if on_progress:
            on_progress(step, detail)

    db_label = get_db_label()
    dialect  = get_db_engine()
    schema   = get_schema()

    # Step 1 — profile
    progress("profiling", "Memuat profil database...")
    try:
        profile_text = format_profile_for_prompt(profile_database())
    except Exception as e:
        profile_text = f"(profil tidak tersedia: {e})"

    # Step 2 — plan questions
    progress("planning", "Merencanakan pertanyaan analitik...")
    plan_raw = _call_llm(
        _PLAN_SYSTEM,
        _PLAN_USER.format(
            db_label=db_label,
            dialect=dialect,
            schema=schema,
            profile=profile_text,
            n=n_questions,
        ),
    )

    try:
        questions = _extract_json(plan_raw).get("questions", [])[:n_questions]
    except Exception as e:
        return {
            "title": f"Insight Report — {db_label}",
            "db_label": db_label,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "narrative": "",
            "executive_summary": "",
            "findings": [],
            "recommendations": "",
            "errors": [f"Gagal mem-parse rencana pertanyaan: {e}"],
        }

    # Step 3 — execute each question
    findings: list[dict] = []
    for i, q in enumerate(questions):
        progress("executing", f"[{i+1}/{len(questions)}] {q.get('question', '')[:70]}")
        result = execute_query(q.get("sql", ""))
        rows   = result.get("rows", [])
        findings.append({
            "question":    q.get("question", ""),
            "sql":         q.get("sql", ""),
            "type":        q.get("type", ""),
            "rows":        rows,
            "error":       result.get("error"),
            "is_anomaly":  _detect_anomaly(rows),
        })

    # Step 4 — build findings text for synthesis
    findings_text = ""
    for i, f in enumerate(findings, 1):
        findings_text += f"\n### {i}. {f['question']}\n"
        if f.get("error"):
            findings_text += f"SQL gagal: {f['error']}\n"
        else:
            findings_text += _summarize_rows(f["rows"]) + "\n"
        if f["is_anomaly"]:
            findings_text += "⚠️ Anomali terdeteksi.\n"

    # Step 5 — synthesize narrative
    progress("synthesizing", "Mensintesis laporan...")
    narrative = _call_llm(
        _SYNTHESIZE_SYSTEM,
        _SYNTHESIZE_USER.format(db_label=db_label, findings_text=findings_text),
    )

    # Extract summary and recommendations sections
    def _section(header: str) -> str:
        m = re.search(
            rf"##\s*{re.escape(header)}\s*(.*?)(?=\n##|$)", narrative, re.DOTALL
        )
        return m.group(1).strip() if m else ""

    progress("done", "")

    return {
        "title":             f"Insight Report — {db_label}",
        "db_label":          db_label,
        "generated_at":      datetime.now().strftime("%Y-%m-%d %H:%M"),
        "narrative":         narrative,
        "executive_summary": _section("Ringkasan Eksekutif"),
        "findings":          findings,
        "recommendations":   _section("Rekomendasi"),
        "errors":            [],
    }
