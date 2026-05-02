"""
tests/test_agent_tools.py — Agent Tool Tests
Tests agent tool definitions and execution logic.
Run: pytest tests/test_agent_tools.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest
import json
from unittest.mock import patch, MagicMock
from app.agent.tools import AGENT_TOOLS, get_agent_system_prompt
from app.agent.agent_runner import run_agent, AgentTrace, TokenUsage


class TestToolDefinitions:

    def test_all_four_tools_defined(self):
        names = [t["name"] for t in AGENT_TOOLS]
        assert "classify_question" in names
        assert "generate_sql"      in names
        assert "execute_sql"       in names
        assert "retry_sql"         in names

    def test_classify_tool_has_required_fields(self):
        tool = next(t for t in AGENT_TOOLS if t["name"] == "classify_question")
        props = tool["input_schema"]["properties"]
        assert "classification" in props
        assert "reasoning"      in props
        assert props["classification"]["enum"] == ["DATA_QUESTION", "GENERAL_CHAT"]

    def test_generate_sql_tool_has_required_fields(self):
        tool = next(t for t in AGENT_TOOLS if t["name"] == "generate_sql")
        props = tool["input_schema"]["properties"]
        assert "sql"        in props
        assert "query_type" in props
        assert "reasoning"  in props

    def test_execute_sql_tool_has_required_fields(self):
        tool = next(t for t in AGENT_TOOLS if t["name"] == "execute_sql")
        assert "sql" in tool["input_schema"]["properties"]

    def test_retry_sql_tool_has_required_fields(self):
        tool  = next(t for t in AGENT_TOOLS if t["name"] == "retry_sql")
        props = tool["input_schema"]["properties"]
        assert "failed_sql"     in props
        assert "error_message"  in props
        assert "fixed_sql"      in props

    def test_system_prompt_contains_tenant(self):
        prompt = get_agent_system_prompt(vkorg="1004", tenant_name="Brennwald")
        assert "1004"      in prompt
        assert "Brennwald" in prompt

    def test_system_prompt_contains_schema(self):
        prompt = get_agent_system_prompt(vkorg="1004")
        assert "order_headers"  in prompt
        assert "customers"      in prompt
        assert "sales_org_code" in prompt


class TestTokenUsage:

    def test_total_calculation(self):
        tu = TokenUsage(total_input=1000, total_output=500)
        assert tu.total == 1500

    def test_cost_calculation(self):
        tu = TokenUsage(total_input=1_000_000, total_output=0)
        assert abs(tu.estimated_cost_usd - 3.0) < 0.01

    def test_to_dict_keys(self):
        tu   = TokenUsage(total_input=100, total_output=50)
        d    = tu.to_dict()
        assert "total_input"        in d
        assert "total_output"       in d
        assert "total"              in d
        assert "estimated_cost_usd" in d


class TestAgentTrace:

    def test_default_trace(self):
        trace = AgentTrace(question="test")
        assert trace.question     == "test"
        assert trace.retry_count  == 0
        assert trace.success      == False
        assert trace.tool_calls   == []

    def test_tool_calls_list(self):
        trace = AgentTrace()
        assert isinstance(trace.tool_calls, list)


class TestAgentWithMocks:
    """Mock-based tests — no API calls."""

    def _make_mock_response(self, stop_reason: str, text: str = "", tool_calls: list = None):
        response = MagicMock()
        response.stop_reason         = stop_reason
        response.usage.input_tokens  = 500
        response.usage.output_tokens = 100

        if stop_reason == "end_turn":
            block      = MagicMock()
            block.text = text
            block.type = "text"
            response.content = [block]

        elif stop_reason == "tool_use" and tool_calls:
            blocks = []
            for tc in tool_calls:
                block      = MagicMock()
                block.type = "tool_use"
                block.name = tc["name"]
                block.input = tc["input"]
                block.id   = f"tool_{tc['name']}"
                blocks.append(block)
            response.content = blocks

        return response

    def test_agent_returns_result_on_end_turn(self):
        mock_response = self._make_mock_response("end_turn", "Here are your top customers.")

        with patch("app.agent.agent_runner.client") as mock_client:
            mock_client.messages.create.return_value = mock_response
            result = run_agent(
                user_question="Who are our top customers?",
                tenant_vkorg="1004",
                tenant_name="Brennwald",
            )

        assert result.success
        assert "top customers" in result.answer.lower()
        assert isinstance(result.trace, AgentTrace)

    def test_agent_tracks_token_usage(self):
        mock_response = self._make_mock_response("end_turn", "Answer here.")

        with patch("app.agent.agent_runner.client") as mock_client:
            mock_client.messages.create.return_value = mock_response
            result = run_agent("Test question", tenant_vkorg="1004", tenant_name="Brennwald")

        assert result.trace.token_usage.total_input  == 500
        assert result.trace.token_usage.total_output == 100
        assert result.trace.token_usage.total        == 600

    def test_agent_handles_general_chat(self):
        """Agent classifies as GENERAL_CHAT and responds without SQL."""
        classify_response = self._make_mock_response("tool_use", tool_calls=[{
            "name":  "classify_question",
            "input": {"classification": "GENERAL_CHAT", "reasoning": "This is a greeting"},
        }])
        final_response = self._make_mock_response("end_turn", "Hello! I can help you query your business data.")

        with patch("app.agent.agent_runner.client") as mock_client:
            mock_client.messages.create.side_effect = [classify_response, final_response]
            result = run_agent("Hi there!", tenant_vkorg="1004", tenant_name="Brennwald")

        assert result.success
        assert result.trace.classification == "GENERAL_CHAT"