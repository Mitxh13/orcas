from scripts.check_repository import scan_text


def test_repository_check_detects_provider_keys_and_model_identifiers():
    gemini_key = "AI" + "za" + "A" * 35
    groq_key = "gs" + "k_" + "B" * 25
    model = "gem" + "ini/example-model"
    assignment = "api_" + "key"
    assert scan_text("example.py", f'{assignment} = "{gemini_key}"')
    assert scan_text("example.py", f'key = "{groq_key}"')
    assert scan_text("example.py", f'LLM_MODEL = "{model}"') == ["model identifier in: example.py"]
    assert scan_text("example.md", f'LLM_MODEL = "{model}"') == []
