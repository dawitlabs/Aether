import os

import pytest

from aether.api.app import Settings
from aether.extraction.llm import LLMClient


@pytest.fixture
def config():
    if os.environ.get("AETHER_TEST_LLM") != "1":
        pytest.skip("Set AETHER_TEST_LLM=1 to call the configured LLM endpoints")
    return Settings.from_env()


def test_live_chat_returns_json_object(config):
    # No cache: the point is a real round trip.
    client = LLMClient(config.llm_base_url, config.llm_model, api_key=config.llm_api_key)
    reply = client.chat_json('Reply with a JSON object {"ok": true}.', "Ready?")
    assert reply.get("ok") is True


def test_live_embed_returns_one_vector_per_text(config):
    client = LLMClient(config.embed_base_url, config.embed_model, api_key=config.embed_api_key)
    vectors = client.embed(["radium", "polonium"])
    assert len(vectors) == 2 and len(vectors[0]) == len(vectors[1]) > 0
