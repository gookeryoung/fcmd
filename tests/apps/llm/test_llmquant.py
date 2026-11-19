"""量化标签解析与 UD 动态量化的档位匹配。"""

from __future__ import annotations

from fcmd.apps.llm._llmquant import (
    exact_quant_match,
    matches_profile,
    parse_quant_tag,
)


def test_parse_basic_quant() -> None:
    tag = parse_quant_tag("model-Q4_K_M.gguf")
    assert tag is not None
    assert (tag.raw, tag.family, tag.variant, tag.dynamic) == ("Q4_K_M", "Q4_K", "M", False)


def test_parse_ud_dynamic_quant() -> None:
    tag = parse_quant_tag("Qwen3-Coder-30B-UD-Q4_K_XL-00001-of-00003.gguf")
    assert tag is not None
    assert tag.raw == "UD-Q4_K_XL"
    assert tag.family == "Q4_K"
    assert tag.variant == "XL"
    assert tag.dynamic is True


def test_parse_ud_without_prefix_still_dynamic() -> None:
    tag = parse_quant_tag("model-Q5_K_XL.gguf")
    assert tag is not None
    assert tag.raw == "Q5_K_XL"
    assert tag.dynamic is True


def test_parse_iq_quant_family() -> None:
    tag = parse_quant_tag("model-UD-IQ4_XS.gguf")
    assert tag is not None
    assert tag.family == "IQ4_XS"
    assert tag.variant == ""
    assert tag.dynamic is True


def test_parse_unknown_returns_none() -> None:
    assert parse_quant_tag("README.md") is None
    assert parse_quant_tag("model.gguf") is None


def test_profile_matches_ud_xl_for_base_quant() -> None:
    assert matches_profile("model-UD-Q4_K_XL.gguf", "Q4_K_M")
    assert matches_profile("model-UD-Q5_K_XL.gguf", "Q5_K_M.gguf")
    assert not matches_profile("model-UD-Q5_K_XL.gguf", "Q4_K_M")


def test_profile_does_not_treat_q4_0_as_q4_k() -> None:
    assert not matches_profile("model-Q4_0.gguf", "Q4_K_M")


def test_exact_quant_match_keeps_ud_distinction() -> None:
    assert exact_quant_match("model-UD-Q4_K_XL.gguf", "UD-Q4_K_XL")
    assert not exact_quant_match("model-UD-Q4_K_XL.gguf", "Q4_K_M")
