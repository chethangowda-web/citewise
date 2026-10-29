import pytest

from app.llm import providers
from app.llm.providers import FallbackGenerator, Generation, LLMUnavailable


class Stub:
    def __init__(self, model, fail=False):
        self.model, self.fail, self.calls = model, fail, 0

    def generate(self, system, prompt):
        self.calls += 1
        if self.fail:
            raise LLMUnavailable(f"{self.model} quota exhausted")
        return Generation("ok [1]", self.model, 5, 2)


def test_uses_primary_when_healthy():
    a, b = Stub("groq"), Stub("gemini")
    assert FallbackGenerator([a, b]).generate("s", "p").model == "groq"
    assert b.calls == 0


def test_falls_back_when_primary_is_out_of_quota():
    a, b = Stub("groq", fail=True), Stub("gemini")
    assert FallbackGenerator([a, b]).generate("s", "p").model == "gemini"


def test_raises_when_every_provider_fails():
    with pytest.raises(LLMUnavailable):
        FallbackGenerator([Stub("a", fail=True), Stub("b", fail=True)]).generate("s", "p")


@pytest.mark.parametrize(
    "provider,groq_key,gemini_key,fallback,expected",
    [
        ("groq", "g", "m", True, FallbackGenerator),
        ("groq", "g", "", True, providers.GroqClient),
        ("groq", "g", "m", False, providers.GroqClient),
        ("gemini", "g", "m", True, FallbackGenerator),
    ],
)
def test_chain_is_built_from_settings(monkeypatch, settings, provider, groq_key, gemini_key, fallback, expected):
    monkeypatch.setattr(providers, "GroqClient", type("GroqClient", (), {"__init__": lambda self: None}))
    monkeypatch.setattr(providers, "GeminiClient", type("GeminiClient", (), {"__init__": lambda self: None}))
    for k, v in dict(llm_provider=provider, groq_api_key=groq_key, gemini_api_key=gemini_key, llm_fallback=fallback).items():
        monkeypatch.setattr(settings, k, v)
    got = providers.get_generator()
    assert isinstance(got, FallbackGenerator if expected is FallbackGenerator else providers.GroqClient)
