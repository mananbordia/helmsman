"""Provider request shapes for the optional field-text helper; no API calls."""

from unittest.mock import Mock

import pytest

from helmsman import model


def test_a_provider_error_keeps_what_the_provider_said(monkeypatch):
    # The status alone is not a diagnosis: the same 403 means an invalid key at one
    # provider and a blocked network at another. A run stopped on a bare "HTTP 403"
    # with no way to tell which, so the provider's own words are kept.
    response = Mock(status_code=403, is_error=True)
    response.text = '{"error":{"message":"Access denied. Please check your network settings."}}'
    client = Mock()
    client.post.return_value = response
    monkeypatch.setattr(model, "CLIENT", client)

    with pytest.raises(RuntimeError) as failure:
        model.post_json("https://api.groq.com/openai/v1/chat/completions", "key", {})

    assert "HTTP 403" in str(failure.value)
    assert "Access denied" in str(failure.value)


def test_a_provider_error_with_no_body_still_reports_the_status(monkeypatch):
    response = Mock(status_code=500, is_error=True, text="")
    client = Mock()
    client.post.return_value = response
    monkeypatch.setattr(model, "CLIENT", client)

    with pytest.raises(RuntimeError, match="HTTP 500"):
        model.post_json("https://example.test/v1/chat/completions", "key", {})


def test_groq_field_text_uses_supported_reasoning_parameters(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("TEXT_MODEL", "openai/gpt-oss-20b")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Alan Turing"}'}}]})
    monkeypatch.setattr(model, "post_json", post)

    value, _ = model.field_text({"goal": "Search for Alan Turing"})

    assert value == "Alan Turing"
    body = post.call_args.args[2]
    assert body["reasoning_effort"] == "low"
    assert body["include_reasoning"] is False
    assert "reasoning" not in body


def test_other_groq_models_do_not_receive_gpt_oss_reasoning_parameters(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("TEXT_MODEL", "llama-3.3-70b-versatile")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Alan Turing"}'}}]})
    monkeypatch.setattr(model, "post_json", post)

    model.field_text({"goal": "Search for Alan Turing"})

    body = post.call_args.args[2]
    assert "reasoning" not in body and "reasoning_effort" not in body and "include_reasoning" not in body
