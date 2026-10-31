from app.core.config import Settings


def test_secrets_are_redacted() -> None:
    settings = Settings(_env_file=None, groq_api_key="private-key")
    assert "private-key" not in repr(settings)
    assert settings.groq_model == "openai/gpt-oss-20b"
