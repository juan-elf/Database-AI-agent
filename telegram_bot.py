"""
telegram_bot.py — Telegram adapter for DataGen (E1–E4).

E1: Read-only Q&A, per-user sessions, HTML formatting
E2: Rate limiting (10 msg/min per user)
E3: CSV upload → classify → inline keyboard confirm → write
E4: /report command → Insight Report as .md + charts as PNG

Runs via long-polling — no webhook or public HTTPS domain required.

Usage:
    python telegram_bot.py

Deploy on VPS:
    nohup python telegram_bot.py > logs/bot.log 2>&1 &
"""
import asyncio
import html
import io
import logging
import os
import re
import time

import pandas as pd
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agent import Agent
from insight_report import generate_report
from router import build_catalog, classify_data
from writer import execute_insert, is_write_configured

load_dotenv()

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_sessions: dict[int, Agent] = {}
_MAX_MSG_LEN = 4096

# Rate limiting
_RATE_LIMIT = int(os.getenv("TELEGRAM_RATE_LIMIT", "10"))
_RATE_WINDOW = 60
_rate_timestamps: dict[int, list[float]] = {}

# CSV upload state — pending confirmation per user
_MAX_CSV_ROWS = 500
_pending_insert: dict[int, dict] = {}
_CB_CONFIRM = "insert_confirm"
_CB_CANCEL  = "insert_cancel"

# Chart generation
_TIME_KW = {"cycle", "date", "time", "year", "month", "day", "week", "hour", "period", "step"}


def _rows_to_png(rows: list[dict]) -> bytes | None:
    """Generate a PNG chart from query result rows. Returns None if not chartable."""
    if not rows or len(rows) < 2:
        return None
    try:
        import plotly.express as px
        df = pd.DataFrame(rows)
        num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        cat_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        time_cols = [c for c in df.columns
                     if any(kw in c.lower() for kw in _TIME_KW) and c in num_cols]
        if not num_cols:
            return None
        if time_cols:
            y_cols = [c for c in num_cols if c not in time_cols]
            if not y_cols:
                return None
            color = cat_cols[0] if cat_cols else None
            fig = px.line(df, x=time_cols[0], y=y_cols[0], color=color)
        elif cat_cols and num_cols:
            fig = px.bar(df, x=cat_cols[0], y=num_cols[0])
        elif len(num_cols) >= 2:
            fig = px.scatter(df, x=num_cols[0], y=num_cols[1])
        else:
            return None
        fig.update_layout(margin=dict(l=50, r=20, t=30, b=50))
        return fig.to_image(format="png", width=800, height=400)
    except Exception:
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_rate_limited(chat_id: int) -> bool:
    now = time.monotonic()
    timestamps = [t for t in _rate_timestamps.get(chat_id, []) if now - t < _RATE_WINDOW]
    _rate_timestamps[chat_id] = timestamps
    if len(timestamps) >= _RATE_LIMIT:
        return True
    timestamps.append(now)
    return False


def _get_agent(chat_id: int) -> Agent:
    if chat_id not in _sessions:
        _sessions[chat_id] = Agent(verbose=False, enable_logging=True)
    return _sessions[chat_id]


def _md_to_html(text: str) -> str:
    """Convert common LLM markdown to Telegram HTML."""
    result = []
    parts = re.split(r'```(?:\w*\n?)?(.*?)```', text, flags=re.DOTALL)
    for i, part in enumerate(parts):
        if i % 2 == 1:
            result.append(f'<pre><code>{html.escape(part.strip())}</code></pre>')
        else:
            part = html.escape(part)
            part = re.sub(r'^#{1,3}\s+(.+)$', r'<b>\1</b>', part, flags=re.MULTILINE)
            part = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', part, flags=re.DOTALL)
            part = re.sub(r'__(.+?)__', r'<b>\1</b>', part)
            part = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', r'<i>\1</i>', part)
            part = re.sub(r'`([^`]+?)`', r'<code>\1</code>', part)
            result.append(part)
    return ''.join(result)


def _split(text: str) -> list[str]:
    """Split long text at line boundaries."""
    if len(text) <= _MAX_MSG_LEN:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        candidate = current + line + "\n"
        if len(candidate) > _MAX_MSG_LEN:
            if current:
                chunks.append(current.rstrip())
            current = line + "\n"
        else:
            current = candidate
    if current.strip():
        chunks.append(current.rstrip())
    return chunks or [text[:_MAX_MSG_LEN]]


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    _sessions.pop(chat_id, None)
    _pending_insert.pop(chat_id, None)
    _get_agent(chat_id)
    await update.message.reply_text(
        "Halo! Saya SQL Agent — tanyakan apa saja tentang database yang terhubung.\n"
        "Ketik /help untuk daftar perintah."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Perintah yang tersedia:\n"
        "/start   — mulai sesi baru\n"
        "/reset   — hapus riwayat percakapan\n"
        "/report  — generate Insight Report otomatis (file .md + chart PNG)\n"
        "/help    — tampilkan pesan ini\n\n"
        "Contoh pertanyaan:\n"
        "• Berapa total data baterai?\n"
        "• Tunjukkan rata-rata SOH per baterai\n"
        "• Ada anomali di data cycle terakhir?\n\n"
        "Upload file CSV untuk mengklasifikasikan dan menyimpan data."
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id in _sessions:
        _sessions[chat_id].reset()
    _pending_insert.pop(chat_id, None)
    await update.message.reply_text("Riwayat percakapan dihapus. Sesi baru dimulai.")


# ── Insight Report command (E4) ───────────────────────────────────────────────

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    if _is_rate_limited(chat_id):
        await update.message.reply_text(
            f"Terlalu banyak permintaan. Maksimal {_RATE_LIMIT} pesan per menit."
        )
        return

    status_msg = await update.message.reply_text(
        "⏳ Generating insight report... (estimasi 30–60 detik)"
    )
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)

    try:
        report = await asyncio.to_thread(generate_report)
    except Exception as e:
        logger.error("generate_report error chat_id %s: %s", chat_id, e)
        await status_msg.edit_text(f"❌ Gagal generate report: {html.escape(str(e))}")
        return

    errors = report.get("errors", [])
    if errors:
        await status_msg.edit_text(
            f"❌ Report selesai dengan error: {html.escape(errors[0])}"
        )
        return

    narrative     = report.get("narrative", "")
    title         = report.get("title", "Insight Report")
    generated_at  = report.get("generated_at", "")
    findings      = report.get("findings", [])

    # Send report as .md document
    md_content = f"# {title}\n_Generated: {generated_at}_\n\n{narrative}"
    safe_ts    = generated_at.replace(" ", "_").replace(":", "-")
    filename   = f"insight_report_{safe_ts}.md"
    await status_msg.delete()
    await context.bot.send_document(
        chat_id=chat_id,
        document=io.BytesIO(md_content.encode("utf-8")),
        filename=filename,
        caption=(
            f"📊 <b>{html.escape(title)}</b>\n"
            f"<i>{generated_at}</i>"
        ),
        parse_mode="HTML",
    )

    # Send charts for each finding that has data
    chart_count = 0
    for finding in findings:
        rows = finding.get("rows", [])
        if not rows:
            continue
        png = await asyncio.to_thread(_rows_to_png, rows)
        if png is None:
            continue
        chart_count += 1
        question = finding.get("question", f"Temuan {chart_count}")
        anomaly_tag = " ⚠️" if finding.get("is_anomaly") else ""
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=io.BytesIO(png),
            caption=f"📈 {html.escape(question[:200])}{anomaly_tag}",
            parse_mode="HTML",
        )


# ── Message handler ───────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    if _is_rate_limited(chat_id):
        await update.message.reply_text(
            f"Terlalu banyak permintaan. Maksimal {_RATE_LIMIT} pesan per menit."
        )
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    agent = _get_agent(chat_id)
    try:
        reply = await asyncio.to_thread(agent.chat, update.message.text)
    except Exception as e:
        logger.error("Agent error for chat_id %s: %s", chat_id, e)
        reply = "Maaf, terjadi error saat memproses pertanyaan. Silakan coba lagi."

    html_reply = _md_to_html(reply)
    for chunk in _split(html_reply):
        try:
            await update.message.reply_text(chunk, parse_mode="HTML")
        except Exception:
            await update.message.reply_text(chunk)


# ── CSV upload handler (E3) ───────────────────────────────────────────────────

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    doc = update.message.document

    if _is_rate_limited(chat_id):
        await update.message.reply_text(
            f"Terlalu banyak permintaan. Maksimal {_RATE_LIMIT} pesan per menit."
        )
        return

    if not (doc.file_name or "").lower().endswith(".csv"):
        await update.message.reply_text("Hanya file CSV (.csv) yang didukung.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # Download + parse
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        buf.seek(0)
        df = pd.read_csv(buf)
    except Exception as e:
        await update.message.reply_text(f"Gagal membaca file CSV: {e}")
        return

    if df.empty:
        await update.message.reply_text("File CSV kosong.")
        return

    if len(df) > _MAX_CSV_ROWS:
        await update.message.reply_text(
            f"File terlalu besar ({len(df):,} baris). Maksimal {_MAX_CSV_ROWS} baris via bot.\n"
            "Gunakan dashboard untuk file yang lebih besar."
        )
        return

    # Classify
    try:
        catalog = await asyncio.to_thread(build_catalog)
        result = await asyncio.to_thread(classify_data, df, catalog)
    except Exception as e:
        await update.message.reply_text(f"Gagal mengklasifikasikan data: {e}")
        return

    table      = result.get("best_match")
    confidence = result.get("confidence", 0)
    reasoning  = result.get("reasoning", "")
    mapping    = result.get("column_mapping", {})
    is_new     = result.get("is_new_table_needed", True)

    # Build reply
    lines = [f"<b>Hasil Klasifikasi</b>",
             f"File: {html.escape(doc.file_name)} ({len(df):,} baris, {len(df.columns)} kolom)",
             ""]

    if table and not is_new:
        emoji = "🟢" if confidence >= 80 else "🟡" if confidence >= 50 else "🔴"
        lines.append(f"{emoji} Tabel: <b>{html.escape(table)}</b> (confidence: {confidence}%)")
    else:
        lines.append("🔴 Tidak cocok dengan tabel yang ada — perlu tabel baru.")

    if reasoning:
        lines.append(f"\n{html.escape(reasoning)}")

    if mapping:
        col_lines = "\n".join(f"  {html.escape(src)} → {html.escape(dst)}"
                               for src, dst in mapping.items())
        lines.append(f"\n<b>Column mapping:</b>\n<pre>{col_lines}</pre>")

    preview_str = html.escape(df.head(5).to_string(index=False))
    lines.append(f"\n<b>Preview (5 baris pertama):</b>\n<pre>{preview_str}</pre>")

    reply_text = "\n".join(lines)

    # Offer write if eligible
    if table and not is_new and confidence >= 80:
        if is_write_configured():
            _pending_insert[chat_id] = {
                "df": df,
                "table": table,
                "column_mapping": mapping,
                "session_id": str(chat_id),
            }
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"✅ Simpan {len(df)} baris ke {table}",
                    callback_data=_CB_CONFIRM,
                ),
                InlineKeyboardButton("❌ Batal", callback_data=_CB_CANCEL),
            ]])
            for chunk in _split(reply_text):
                await update.message.reply_text(chunk, parse_mode="HTML",
                                                reply_markup=keyboard)
        else:
            lines.append(
                "\n💡 <i>Write belum dikonfigurasi. "
                "Set WRITE_DATABASE_URL untuk mengaktifkan penyimpanan.</i>"
            )
            for chunk in _split("\n".join(lines)):
                await update.message.reply_text(chunk, parse_mode="HTML")
    else:
        for chunk in _split(reply_text):
            await update.message.reply_text(chunk, parse_mode="HTML")


# ── Inline keyboard callback (E3) ─────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id

    if query.data == _CB_CANCEL:
        _pending_insert.pop(chat_id, None)
        await query.edit_message_text("❌ Dibatalkan.")
        return

    if query.data == _CB_CONFIRM:
        pending = _pending_insert.pop(chat_id, None)
        if not pending:
            await query.edit_message_text(
                "Sesi insert sudah kadaluarsa. Upload ulang file CSV."
            )
            return

        await query.edit_message_text("⏳ Menyimpan data...")

        try:
            result = await asyncio.to_thread(
                execute_insert,
                pending["df"],
                pending["table"],
                pending["column_mapping"],
                session_id=pending["session_id"],
            )
        except Exception as e:
            logger.error("execute_insert error chat_id %s: %s", chat_id, e)
            await query.edit_message_text(f"❌ Error tidak terduga: {e}")
            return

        if result.get("success"):
            rows  = result.get("rows_inserted", 0)
            tbl   = result.get("table", "")
            audit = result.get("audit_file", "")
            await query.edit_message_text(
                f"✅ <b>{rows} baris berhasil disimpan ke <code>{html.escape(tbl)}</code>.</b>\n"
                f"Audit log: <code>{html.escape(str(audit))}</code>",
                parse_mode="HTML",
            )
        else:
            errors = result.get("errors") or ["Unknown error"]
            await query.edit_message_text(f"❌ Gagal: {html.escape(errors[0])}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN tidak ditemukan. Set di .env atau environment.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(MessageHandler(filters.Document.FileExtension("csv"), handle_document))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started — long-polling aktif.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
