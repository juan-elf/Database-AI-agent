import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


class ConversationLogger:
    def __init__(self):
        self.session_id = uuid.uuid4().hex[:8]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = LOG_DIR / f"session_{timestamp}_{self.session_id}.jsonl"
        self._write({"event": "session_start", "session_id": self.session_id})

    def _write(self, data: dict[str, Any]):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            **data
        }
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def log_user_message(self, message: str):
        self._write({"event": "user_message", "content": message})

    def log_tool_call(self, tool_name: str, arguments: dict, result_preview: str):
        self._write({
            "event": "tool_call",
            "tool": tool_name,
            "arguments": arguments,
            "result_preview": result_preview[:500],
        })

    def log_assistant_message(self, content: str, token_usage: dict | None = None):
        self._write({
            "event": "assistant_message",
            "content": content,
            "token_usage": token_usage,
        })

    def log_error(self, error_type: str, error_message: str):
        self._write({
            "event": "error",
            "error_type": error_type,
            "error_message": error_message,
        })

    def get_log_path(self) -> str:
        return str(self.log_file)
