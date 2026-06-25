"""
Tests for agent.py — domain utils, build_system_prompt, Agent class, retry.

All tests mock the OpenAI client; no real API calls are made.

Run with:
    pytest tests/test_agent.py -v
"""
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

import database


# ── Helpers ───────────────────────────────────────────────────────────────────

def _response(content=None, tool_calls=None, prompt_tokens=10, completion_tokens=5):
    """Build a minimal fake OpenAI ChatCompletion response object."""
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    msg   = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)


def _tool_call(tc_id="tc_1", name="execute_sql", args=None):
    """Build a fake tool_call entry (mimics openai ToolCall)."""
    args = args or {"sql": "SELECT 1 AS x"}
    func = SimpleNamespace(name=name, arguments=json.dumps(args))
    return SimpleNamespace(id=tc_id, function=func)


def _connection_error():
    """Create a real APIConnectionError instance for retry tests."""
    from openai import APIConnectionError
    req = httpx.Request("POST", "https://api.minimax.io/v1/chat/completions")
    return APIConnectionError(request=req)


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def db_env(tmp_path):
    """
    Create a minimal SQLite DB and set it as active database.
    Patches agent.get_schema and agent.web_available so Agent.__init__
    does not need a Tavily key or a live schema call.
    """
    db_file = tmp_path / "agent_test.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE items (id INTEGER, name TEXT)")
    conn.commit()
    conn.close()
    database.set_database(db_file)

    with (
        patch("agent.get_schema", return_value="TABLE: items\n  - id: INTEGER\n  - name: TEXT"),
        patch("agent.web_available", return_value=False),
    ):
        yield tmp_path


# ── Domain utilities ──────────────────────────────────────────────────────────

class TestListAvailableDomains:

    def test_returns_empty_when_dir_missing(self, tmp_path, monkeypatch):
        import agent
        monkeypatch.setattr(agent, "DOMAINS_DIR", tmp_path / "no_dir")
        assert agent.list_available_domains() == []

    def test_returns_sorted_stems(self, tmp_path, monkeypatch):
        import agent
        (tmp_path / "zebra.md").write_text("z", encoding="utf-8")
        (tmp_path / "alpha.md").write_text("a", encoding="utf-8")
        monkeypatch.setattr(agent, "DOMAINS_DIR", tmp_path)
        assert agent.list_available_domains() == ["alpha", "zebra"]

    def test_ignores_non_md_files(self, tmp_path, monkeypatch):
        import agent
        (tmp_path / "battery.md").write_text("b", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("t", encoding="utf-8")
        monkeypatch.setattr(agent, "DOMAINS_DIR", tmp_path)
        assert agent.list_available_domains() == ["battery"]


class TestLoadDomainPack:

    def test_returns_content_when_found(self, tmp_path, monkeypatch):
        import agent
        (tmp_path / "battery.md").write_text("battery knowledge", encoding="utf-8")
        monkeypatch.setattr(agent, "DOMAINS_DIR", tmp_path)
        assert agent.load_domain_pack("battery") == "battery knowledge"

    def test_returns_none_when_missing(self, tmp_path, monkeypatch):
        import agent
        monkeypatch.setattr(agent, "DOMAINS_DIR", tmp_path)
        assert agent.load_domain_pack("nonexistent") is None


# ── build_system_prompt ───────────────────────────────────────────────────────

class TestBuildSystemPrompt:

    def test_contains_generic_instructions(self, db_env):
        import agent
        prompt = agent.build_system_prompt()
        assert "DataGen" in prompt

    def test_contains_schema(self, db_env):
        import agent
        prompt = agent.build_system_prompt()
        assert "TABLE: items" in prompt

    def test_with_valid_domain_includes_content(self, db_env, monkeypatch):
        import agent
        (db_env / "battery.md").write_text("BATTERY_CONTENT", encoding="utf-8")
        monkeypatch.setattr(agent, "DOMAINS_DIR", db_env)
        prompt = agent.build_system_prompt("battery")
        assert "BATTERY_CONTENT" in prompt
        assert "DOMAIN KNOWLEDGE: battery" in prompt

    def test_with_missing_domain_shows_fallback(self, db_env, monkeypatch):
        import agent
        monkeypatch.setattr(agent, "DOMAINS_DIR", db_env)
        prompt = agent.build_system_prompt("nonexistent")
        assert "not found" in prompt


# ── Agent.__init__ ────────────────────────────────────────────────────────────

class TestAgentInit:

    def test_token_counts_start_at_zero(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)
        stats = a.get_stats()
        assert stats["total_input_tokens"] == 0
        assert stats["total_output_tokens"] == 0
        assert stats["total_tokens"] == 0

    def test_logging_disabled_sets_logger_none(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)
        assert a.logger is None

    def test_messages_initialized_with_single_system_message(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)
        assert len(a.messages) == 1
        assert a.messages[0]["role"] == "system"

    def test_domain_stored_on_instance(self, db_env):
        import agent
        a = agent.Agent(domain="battery", verbose=False, enable_logging=False)
        assert a.domain == "battery"

    def test_system_prompt_contains_generic_instructions(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)
        assert "DataGen" in a.system_prompt


# ── Agent.chat ────────────────────────────────────────────────────────────────

class TestAgentChat:

    def test_direct_answer_returned(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)
        resp = _response(content="42 items.", tool_calls=None)

        with patch("agent._call_api_with_retry", return_value=resp):
            answer = a.chat("How many items?")

        assert answer == "42 items."

    def test_history_grows_by_user_and_assistant(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)
        resp = _response(content="Done.", tool_calls=None)

        with patch("agent._call_api_with_retry", return_value=resp):
            a.chat("hi")

        # system + user + assistant
        assert len(a.messages) == 3
        assert a.messages[1]["role"] == "user"
        assert a.messages[2]["role"] == "assistant"

    def test_tokens_accumulated(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)
        resp = _response(content="ok", prompt_tokens=30, completion_tokens=12)

        with patch("agent._call_api_with_retry", return_value=resp):
            a.chat("test")

        assert a.total_input_tokens == 30
        assert a.total_output_tokens == 12

    def test_tool_call_dispatched_then_final_answer(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)
        tc       = _tool_call(tc_id="tc_1", name="execute_sql", args={"sql": "SELECT 1 AS x"})
        first    = _response(content=None, tool_calls=[tc])
        final    = _response(content="The result is 1.", tool_calls=None)
        tool_ret = json.dumps({"success": True, "rows": [{"x": 1}], "row_count": 1})

        with (
            patch("agent._call_api_with_retry", side_effect=[first, final]),
            patch("agent.call_tool", return_value=tool_ret) as mock_ct,
        ):
            answer = a.chat("What is 1?")

        assert answer == "The result is 1."
        mock_ct.assert_called_once_with("execute_sql", {"sql": "SELECT 1 AS x"})

    def test_invalid_json_tool_args_does_not_crash(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)
        bad_tc = SimpleNamespace(
            id="tc_bad",
            function=SimpleNamespace(name="execute_sql", arguments="NOT VALID JSON {{{"),
        )
        first = _response(content=None, tool_calls=[bad_tc])
        final = _response(content="Recovered.", tool_calls=None)

        with patch("agent._call_api_with_retry", side_effect=[first, final]):
            answer = a.chat("test")

        assert answer == "Recovered."

    def test_max_iterations_returns_warning(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)
        tc      = _tool_call()
        looping = _response(content=None, tool_calls=[tc])

        with (
            patch("agent._call_api_with_retry", return_value=looping),
            patch("agent.call_tool", return_value='{"success":true,"rows":[]}'),
        ):
            answer = a.chat("loop forever")

        assert "Max iterations" in answer

    def test_api_exception_returns_error_string(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)

        with patch("agent._call_api_with_retry", side_effect=RuntimeError("network down")):
            answer = a.chat("test")

        assert "⚠️" in answer
        assert "API failed" in answer


# ── Agent.reset ───────────────────────────────────────────────────────────────

class TestAgentReset:

    def test_reset_removes_conversation_history(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)
        resp = _response(content="Done.")
        with patch("agent._call_api_with_retry", return_value=resp):
            a.chat("hello")
        assert len(a.messages) > 1

        a.reset()
        assert len(a.messages) == 1
        assert a.messages[0]["role"] == "system"

    def test_reset_preserves_system_prompt(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)
        original_prompt = a.system_prompt
        a.reset()
        assert a.messages[0]["content"] == original_prompt

    def test_reset_zeroes_token_counts(self, db_env):
        import agent
        a = agent.Agent(verbose=False, enable_logging=False)
        a.total_input_tokens  = 500
        a.total_output_tokens = 200
        a.reset()
        assert a.total_input_tokens  == 0
        assert a.total_output_tokens == 0


# ── _call_api_with_retry ──────────────────────────────────────────────────────

class TestCallApiWithRetry:

    def test_returns_response_on_first_success(self):
        import agent
        fake = _response(content="ok")
        with patch("agent.client.chat.completions.create", return_value=fake) as mock_api:
            result = agent._call_api_with_retry([], [])
        assert result is fake
        assert mock_api.call_count == 1

    def test_retries_on_connection_error_then_succeeds(self):
        import agent
        err  = _connection_error()
        fake = _response(content="ok")
        with (
            patch("agent.client.chat.completions.create", side_effect=[err, fake]),
            patch("agent.time.sleep") as mock_sleep,
        ):
            result = agent._call_api_with_retry([], [])
        assert result is fake
        mock_sleep.assert_called_once()

    def test_raises_after_exhausting_all_retries(self):
        from openai import APIConnectionError
        import agent
        err = _connection_error()
        with (
            patch("agent.client.chat.completions.create", side_effect=err),
            patch("agent.time.sleep"),
        ):
            with pytest.raises(APIConnectionError):
                agent._call_api_with_retry([], [])

    def test_sleep_called_between_retries_not_on_last(self):
        import agent
        err = _connection_error()
        with (
            patch("agent.client.chat.completions.create", side_effect=err),
            patch("agent.time.sleep") as mock_sleep,
        ):
            with pytest.raises(Exception):
                agent._call_api_with_retry([], [])
        # MAX_RETRIES=3: sleep on attempt 0 and 1, NOT on attempt 2 (last)
        assert mock_sleep.call_count == agent.MAX_RETRIES - 1
