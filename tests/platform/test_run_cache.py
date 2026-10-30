from pathlib import Path

from libs.orcas_common.run_cache import RedisRunCache, prompt_fingerprint


def test_cache_key_tracks_normalized_input_index_and_prompts(monkeypatch, tmp_path: Path):
    prompt = tmp_path / "draft.yaml"
    prompt.write_text("version: v1")
    cache = object.__new__(RedisRunCache)
    cache.index_url = "http://retrieval"
    cache.prompts = prompt_fingerprint(tmp_path)

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"index_version": "index-v1"}

    monkeypatch.setattr("libs.orcas_common.run_cache.httpx.get", lambda *args, **kwargs: Response())
    payload = {"client_id": "demo-client", "text": "Bank   interest delayed", "channel": "web"}
    key = cache.key_for(payload)
    assert key == cache.key_for({**payload, "text": " bank interest delayed "})
    assert key != cache.key_for({**payload, "client_id": "other-client"})
    cache.prompts = "new-prompt-version"
    assert key != cache.key_for(payload)
