"""Provider request shapes for the optional field-text helper; no API calls."""

from unittest.mock import Mock

from jev_ultrafast import model


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
