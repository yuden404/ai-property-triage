"""Unit tests for the shared AWS + Gemini helpers (boto3 / genai fully mocked)."""
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_boto_config_has_bounded_timeouts():
    import shared.aws_utils as aws
    cfg = aws._BOTO_CONFIG
    assert cfg.connect_timeout == 5
    assert cfg.read_timeout == 30
    assert cfg.retries["max_attempts"] == 2


def test_client_applies_boto_config(monkeypatch):
    import shared.aws_utils as aws
    captured = {}

    class FakeSession:
        def client(self, service, config=None):
            captured["service"] = service
            captured["config"] = config
            return "CLIENT"

    monkeypatch.setattr(aws, "session", lambda: FakeSession())
    assert aws.client("s3") == "CLIENT"
    assert captured["service"] == "s3"
    assert captured["config"] is aws._BOTO_CONFIG  # every client gets the timeouts


def test_gemini_key_raw_string_secret(monkeypatch):
    import shared.aws_utils as aws
    aws.get_gemini_api_key.cache_clear()
    fake = MagicMock()
    fake.get_secret_value.return_value = {"SecretString": "sk-RAW"}
    monkeypatch.setattr(aws, "client", lambda service: fake)
    assert aws.get_gemini_api_key() == "sk-RAW"
    aws.get_gemini_api_key()  # second call should be served from the lru_cache
    assert fake.get_secret_value.call_count == 1
    aws.get_gemini_api_key.cache_clear()


def test_gemini_key_json_secret(monkeypatch):
    import shared.aws_utils as aws
    aws.get_gemini_api_key.cache_clear()
    fake = MagicMock()
    fake.get_secret_value.return_value = {"SecretString": '{"api_key": "sk-JSON"}'}
    monkeypatch.setattr(aws, "client", lambda service: fake)
    assert aws.get_gemini_api_key() == "sk-JSON"
    aws.get_gemini_api_key.cache_clear()


def test_generate_passes_prompt_and_temperature(monkeypatch):
    import shared.gemini_utils as gem
    fake_models = MagicMock()
    fake_models.generate_content.return_value = SimpleNamespace(text="  hello  ")
    monkeypatch.setattr(gem, "client", lambda: SimpleNamespace(models=fake_models))
    assert gem.generate("PROMPT", temperature=0.2) == "hello"  # stripped
    kwargs = fake_models.generate_content.call_args.kwargs
    assert kwargs["model"] == gem.GEMINI_MODEL
    assert kwargs["contents"] == "PROMPT"
    assert kwargs["config"]["temperature"] == 0.2


def test_generate_none_text_returns_empty(monkeypatch):
    import shared.gemini_utils as gem
    fake_models = MagicMock()
    fake_models.generate_content.return_value = SimpleNamespace(text=None)
    monkeypatch.setattr(gem, "client", lambda: SimpleNamespace(models=fake_models))
    assert gem.generate("x") == ""


def test_module_defaults():
    import shared.aws_utils as aws
    import shared.gemini_utils as gem
    assert aws.REGION  # non-empty
    assert gem.GEMINI_MODEL  # non-empty
    assert gem.GEMINI_TIMEOUT_MS == 30000
