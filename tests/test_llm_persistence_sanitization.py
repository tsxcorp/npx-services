"""
Tests for llm_persistence.py sanitization — security-critical sanitizer.
Run: cd nexpo-services && python -m pytest tests/test_llm_persistence_sanitization.py -v
"""
import json

import pytest

from app.services.llm_persistence import sanitize_messages, _sanitize_value


class TestSanitizeValue:
    """Tests for _sanitize_value — recursive sensitive key stripping."""

    def test_strips_user_token_top_level(self):
        """Strips user_token at top level."""
        data = {"user_token": "secret-123", "name": "Alice"}
        result = _sanitize_value(data)

        assert "user_token" not in result
        assert result["name"] == "Alice"

    def test_strips_admin_token(self):
        """Strips admin_token."""
        data = {"admin_token": "admin-secret", "action": "create"}
        result = _sanitize_value(data)

        assert "admin_token" not in result
        assert result["action"] == "create"

    def test_strips_api_key(self):
        """Strips api_key."""
        data = {"api_key": "key-xyz", "endpoint": "/api"}
        result = _sanitize_value(data)

        assert "api_key" not in result
        assert result["endpoint"] == "/api"

    def test_strips_password(self):
        """Strips password."""
        data = {"password": "pass123", "username": "bob"}
        result = _sanitize_value(data)

        assert "password" not in result
        assert result["username"] == "bob"

    def test_strips_secret(self):
        """Strips secret."""
        data = {"secret": "confidential", "type": "oauth"}
        result = _sanitize_value(data)

        assert "secret" not in result
        assert result["type"] == "oauth"

    def test_strips_token_generic(self):
        """Strips generic token field."""
        data = {"token": "bearer-xyz", "scope": "read"}
        result = _sanitize_value(data)

        assert "token" not in result
        assert result["scope"] == "read"

    def test_nested_dict_sanitization(self):
        """Strips sensitive keys in nested dicts."""
        data = {
            "outer": "value",
            "nested": {
                "user_token": "secret",
                "inner": "keep",
            },
        }
        result = _sanitize_value(data)

        assert result["outer"] == "value"
        assert "user_token" not in result["nested"]
        assert result["nested"]["inner"] == "keep"

    def test_deeply_nested_sanitization(self):
        """Strips sensitive keys in deeply nested structures."""
        data = {
            "level1": {
                "level2": {
                    "api_key": "secret",
                    "level3": {
                        "admin_token": "admin-secret",
                        "data": "keep",
                    },
                },
            },
        }
        result = _sanitize_value(data)

        assert "api_key" not in result["level1"]["level2"]
        assert "admin_token" not in result["level1"]["level2"]["level3"]
        assert result["level1"]["level2"]["level3"]["data"] == "keep"

    def test_sanitize_list_of_dicts(self):
        """Strips sensitive keys from dicts in lists."""
        data = [
            {"user_token": "secret1", "name": "Alice"},
            {"api_key": "secret2", "name": "Bob"},
            {"name": "Charlie"},
        ]
        result = _sanitize_value(data)

        assert "user_token" not in result[0]
        assert result[0]["name"] == "Alice"
        assert "api_key" not in result[1]
        assert result[1]["name"] == "Bob"
        assert result[2]["name"] == "Charlie"

    def test_sanitize_list_nested_in_dict(self):
        """Strips sensitive keys from lists nested in dicts."""
        data = {
            "items": [
                {"user_token": "secret", "id": 1},
                {"admin_token": "secret", "id": 2},
            ],
        }
        result = _sanitize_value(data)

        assert "user_token" not in result["items"][0]
        assert "admin_token" not in result["items"][1]
        assert result["items"][0]["id"] == 1

    def test_empty_dict(self):
        """Empty dict → empty dict."""
        result = _sanitize_value({})
        assert result == {}

    def test_empty_list(self):
        """Empty list → empty list."""
        result = _sanitize_value([])
        assert result == []

    def test_none_value_preserved(self):
        """None values preserved."""
        data = {"name": None, "api_key": "secret"}
        result = _sanitize_value(data)

        assert "api_key" not in result
        assert result["name"] is None

    def test_scalar_values_preserved(self):
        """Scalar values (string, int, bool) preserved."""
        data = {"name": "Alice", "count": 42, "active": True}
        result = _sanitize_value(data)

        assert result == {"name": "Alice", "count": 42, "active": True}

    def test_non_sensitive_keys_preserved(self):
        """Non-sensitive keys kept exactly."""
        data = {
            "user_id": "u123",
            "tenant_id": "t456",
            "action": "read",
            "metadata": {"key": "value"},
        }
        result = _sanitize_value(data)

        assert result == data


class TestSanitizeMessages:
    """Tests for sanitize_messages — full message history sanitization."""

    def test_sanitize_tool_calls_arguments(self):
        """Sanitizes sensitive keys in tool_calls function arguments."""
        messages = [
            {
                "role": "assistant",
                "content": "Calling tool",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "create_user",
                            "arguments": json.dumps({
                                "name": "Alice",
                                "api_key": "secret-xyz",
                                "password": "pass123",
                            }),
                        },
                    },
                ],
            },
        ]

        result = sanitize_messages(messages)

        # Extract sanitized arguments
        args_str = result[0]["tool_calls"][0]["function"]["arguments"]
        args = json.loads(args_str)

        assert "api_key" not in args
        assert "password" not in args
        assert args["name"] == "Alice"

    def test_sanitize_tool_result_content(self):
        """Sanitizes sensitive keys in tool result content."""
        messages = [
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": json.dumps({
                    "status": "ok",
                    "user_token": "secret-token",
                    "admin_token": "admin-secret",
                    "result": "success",
                }),
            },
        ]

        result = sanitize_messages(messages)

        content_str = result[0]["content"]
        content = json.loads(content_str)

        assert "user_token" not in content
        assert "admin_token" not in content
        assert content["result"] == "success"

    def test_non_tool_messages_unchanged(self):
        """User/assistant messages without tool_calls unchanged."""
        messages = [
            {
                "role": "user",
                "content": "Hello",
            },
            {
                "role": "assistant",
                "content": "Hi there",
            },
        ]

        result = sanitize_messages(messages)

        assert result[0]["content"] == "Hello"
        assert result[1]["content"] == "Hi there"

    def test_sanitize_multiple_tool_calls(self):
        """Sanitizes multiple tool_calls in one message."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "tool_a",
                            "arguments": json.dumps({"api_key": "secret1", "x": 1}),
                        },
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "tool_b",
                            "arguments": json.dumps({"password": "secret2", "y": 2}),
                        },
                    },
                ],
            },
        ]

        result = sanitize_messages(messages)

        args1 = json.loads(result[0]["tool_calls"][0]["function"]["arguments"])
        args2 = json.loads(result[0]["tool_calls"][1]["function"]["arguments"])

        assert "api_key" not in args1
        assert "password" not in args2
        assert args1["x"] == 1
        assert args2["y"] == 2

    def test_sanitize_tool_calls_invalid_json_skipped(self):
        """Invalid JSON in arguments → skipped gracefully."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "tool",
                            "arguments": "not valid json {",
                        },
                    },
                ],
            },
        ]

        # Should not raise, just preserve invalid JSON
        result = sanitize_messages(messages)
        assert result[0]["tool_calls"][0]["function"]["arguments"] == "not valid json {"

    def test_deep_copy_original_unchanged(self):
        """Original messages list unchanged after sanitization."""
        original = [
            {
                "role": "tool",
                "content": json.dumps({"api_key": "secret", "x": 1}),
            },
        ]

        original_copy = json.loads(json.dumps(original))
        result = sanitize_messages(original)

        # Original should still have api_key
        assert "api_key" in json.loads(original[0]["content"])
        # Result should not
        assert "api_key" not in json.loads(result[0]["content"])

    def test_mixed_message_sequence(self):
        """Full conversation with user + assistant + tool messages."""
        messages = [
            {"role": "user", "content": "What's my password?"},
            {
                "role": "assistant",
                "content": "Checking...",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_user",
                            "arguments": json.dumps({"user_token": "secret"}),
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": json.dumps({"password": "pass123", "user_id": "u1"}),
            },
        ]

        result = sanitize_messages(messages)

        # User message unchanged
        assert result[0]["content"] == "What's my password?"
        # Tool_call args sanitized
        args = json.loads(result[1]["tool_calls"][0]["function"]["arguments"])
        assert "user_token" not in args
        # Tool result sanitized
        content = json.loads(result[2]["content"])
        assert "password" not in content
        assert content["user_id"] == "u1"

    def test_empty_messages_list(self):
        """Empty messages list → empty list."""
        result = sanitize_messages([])
        assert result == []

    def test_sensitive_keys_case_sensitive(self):
        """Key matching is case-sensitive (PASSWORD != password)."""
        data = {"PASSWORD": "upper", "password": "lower", "api_key": "secret"}
        result = _sanitize_value(data)

        # PASSWORD kept (different case), password removed, api_key removed
        assert "PASSWORD" in result
        assert "password" not in result
        assert "api_key" not in result
