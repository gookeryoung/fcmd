"""多模态（mmproj）配对、启动参数、附件序列化与降级重试测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.locllm import llmchat, llmenv, llmmodel, llmserve
from fcmd.apps.locllm.llmbase import (
    PROFILES,
    EngineInfo,
    LaunchPlan,
    LlamaCppBackend,
    ServeError,
    ServeState,
)
from fcmd.apps.locllm.llmchat import Attachment, ChatDelta, ChatMessage, ChatSession

# ---------------------------------------------------------------------- #
# 测试数据
# ---------------------------------------------------------------------- #


def _png(width: int = 64, height: int = 64, padding: int = 32) -> bytes:
    """构造一个仅含头部的合法 PNG（足够 image_size 解析）。"""
    header = (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    return header + b"\x00" * padding


def _jpeg(width: int = 32, height: int = 24) -> bytes:
    """构造一个带 SOF0 段的合法 JPEG 头部。"""
    return (
        b"\xff\xd8"
        + b"\xff\xc0"
        + (17).to_bytes(2, "big")
        + b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x00" * 16
    )


def _model(tmp_path: Path, name: str = "Qwen3-VL-8B-Q4_K_M.gguf", size: int = 4096) -> Path:
    path = tmp_path / name
    path.write_bytes(b"0" * size)
    return path


def _mmproj(tmp_path: Path, name: str = "mmproj-Qwen3-VL-8B-f16.gguf", size: int = 4096) -> Path:
    path = tmp_path / name
    path.write_bytes(b"0" * size)
    return path


def _plan(tmp_path: Path, **kwargs: Any) -> LaunchPlan:
    model = _model(tmp_path)
    engine = tmp_path / "llama-server.exe"
    engine.write_bytes(b"")
    options: dict[str, Any] = {
        "name": "qwen",
        "model": model,
        "profile": PROFILES["balanced"],
        "context": 8192,
        "host": "127.0.0.1",
        "port": 8080,
        "alias": "qwen",
        "api_key": "k",
        "threads": 8,
        "engine": EngineInfo("b1", engine),
        "log": tmp_path / "log.txt",
    }
    options.update(kwargs)
    return LaunchPlan(**options)  # type: ignore[arg-type]


def _state(tmp_path: Path, **kwargs: Any) -> ServeState:
    options: dict[str, Any] = {
        "name": "qwen",
        "pid": 1,
        "create_time": 1.0,
        "host": "127.0.0.1",
        "port": 8080,
        "model": "m.gguf",
        "profile": "balanced",
        "context": 8192,
        "engine": "b1",
        "alias": "qwen",
        "api_key": "k",
        "log": str(tmp_path / "log.txt"),
        "started_at": "2026-01-01T00:00:00",
    }
    options.update(kwargs)
    return ServeState(**options)  # type: ignore[arg-type]


def _endpoint() -> llmchat.Endpoint:
    return llmchat.Endpoint(base_url="http://127.0.0.1:8080", api_key="k", model="m")


# ---------------------------------------------------------------------- #
# 配对打分
# ---------------------------------------------------------------------- #
class TestPairScore:
    def test_same_dir_same_size_wins(self, tmp_path: Path) -> None:
        score, reasons = llmmodel.pair_score(_model(tmp_path), _mmproj(tmp_path))
        assert score >= llmmodel.MMPROJ_MIN_SCORE
        assert any("同目录" in reason for reason in reasons)
        assert any("参数量一致" in reason for reason in reasons)

    def test_size_conflict_is_rejected(self, tmp_path: Path) -> None:
        score, reasons = llmmodel.pair_score(_model(tmp_path), _mmproj(tmp_path, "mmproj-Qwen3-VL-4B-f16.gguf"))
        assert score < 0
        assert any("参数量冲突" in reason for reason in reasons)

    def test_generic_name_in_same_dir_gets_bonus(self, tmp_path: Path) -> None:
        score, reasons = llmmodel.pair_score(_model(tmp_path), _mmproj(tmp_path, "mmproj.gguf"))
        assert any("无同名特征" in reason for reason in reasons)
        assert score > 0

    def test_mmproj_subdirectory_gets_bonus(self, tmp_path: Path) -> None:
        model = _model(tmp_path)
        folder = tmp_path / "mmproj"
        folder.mkdir()
        target = folder / "Qwen3-VL-8B-projector.gguf"
        target.write_bytes(b"0" * 4096)
        score, reasons = llmmodel.pair_score(model, target)
        assert any("子目录" in reason for reason in reasons)
        assert score >= 0


class TestFindMmproj:
    def test_auto_pairs_same_directory(self, tmp_path: Path) -> None:
        model = _model(tmp_path)
        expected = _mmproj(tmp_path)
        candidate = llmmodel.find_mmproj(model)
        assert candidate is not None
        assert candidate.path == expected

    def test_ignores_unrelated_candidates(self, tmp_path: Path) -> None:
        model = _model(tmp_path, "Llama-3-8B.gguf")
        _mmproj(tmp_path, "mmproj-Qwen3-VL-4B-f16.gguf")
        assert llmmodel.find_mmproj(model) is None

    def test_discovers_mmproj_subdirectory(self, tmp_path: Path) -> None:
        model = _model(tmp_path)
        folder = tmp_path / "mmproj"
        folder.mkdir()
        expected = folder / "Qwen3-VL-8B-projector.gguf"
        expected.write_bytes(b"0" * (21 * 1024 * 1024))
        candidate = llmmodel.find_mmproj(model)
        assert candidate is not None
        assert candidate.path == expected

    def test_unique_candidate_gets_minimum_score(self, tmp_path: Path) -> None:
        model = _model(tmp_path, "m.gguf")
        _mmproj(tmp_path, "mmproj.gguf")
        candidate = llmmodel.find_mmproj(model)
        assert candidate is not None
        assert candidate.score >= llmmodel.MMPROJ_MIN_SCORE
        assert "唯一候选" in candidate.reasons

    def test_no_candidates_returns_none(self, tmp_path: Path) -> None:
        assert llmmodel.find_mmproj(_model(tmp_path)) is None


class TestResolveMmproj:
    def test_auto_returns_path(self, tmp_path: Path) -> None:
        model = _model(tmp_path)
        expected = _mmproj(tmp_path)
        assert llmmodel.resolve_mmproj(model, "auto") == expected

    def test_off_disables(self, tmp_path: Path) -> None:
        assert llmmodel.resolve_mmproj(_model(tmp_path), "off") is None

    def test_explicit_path(self, tmp_path: Path) -> None:
        model = _model(tmp_path)
        target = _mmproj(tmp_path, "custom.gguf")
        assert llmmodel.resolve_mmproj(model, str(target)) == target.resolve()

    def test_missing_path_reports_error(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="不存在"):
            llmmodel.resolve_mmproj(_model(tmp_path), str(tmp_path / "nope.gguf"))

    def test_non_gguf_reports_error(self, tmp_path: Path) -> None:
        other = tmp_path / "weights.bin"
        other.write_bytes(b"x")
        with pytest.raises(ServeError, match="gguf"):
            llmmodel.resolve_mmproj(_model(tmp_path), str(other))


# ---------------------------------------------------------------------- #
# 启动参数
# ---------------------------------------------------------------------- #
class TestLaunchArgs:
    def test_argv_injects_mmproj(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, mmproj=_mmproj(tmp_path))
        argv = LlamaCppBackend().build_argv(plan)
        assert argv[argv.index("--mmproj") + 1] == str(plan.mmproj)
        assert plan.vision is True

    def test_argv_without_mmproj_is_unchanged(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path)
        argv = LlamaCppBackend().build_argv(plan)
        assert "--mmproj" not in argv
        assert plan.vision is False

    def test_vision_forces_single_slot(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, parallel=4, mmproj=_mmproj(tmp_path))
        argv = LlamaCppBackend().build_argv(plan)
        assert argv[argv.index("-np") + 1] == "1"

    def test_text_mode_keeps_parallel(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, parallel=4)
        argv = LlamaCppBackend().build_argv(plan)
        assert argv[argv.index("-np") + 1] == "4"

    def test_plan_vram_includes_projector(self, tmp_path: Path) -> None:
        plain = llmmodel.plan_vram(_plan(tmp_path))
        with_vision = llmmodel.plan_vram(_plan(tmp_path, mmproj=_mmproj(tmp_path)))
        assert with_vision > plain


# ---------------------------------------------------------------------- #
# 附件与多模态 payload
# ---------------------------------------------------------------------- #
class TestAttachment:
    def test_png_is_accepted(self, tmp_path: Path) -> None:
        path = tmp_path / "a.png"
        path.write_bytes(_png())
        attachment = Attachment.image(path)
        assert attachment.kind == "image"
        assert attachment.mime == "image/png"
        assert attachment.size == path.stat().st_size
        assert attachment.path == path

    def test_jpeg_size_is_parsed(self, tmp_path: Path) -> None:
        path = tmp_path / "a.jpg"
        path.write_bytes(_jpeg())
        attachment = Attachment.image(path)
        assert attachment.mime == "image/jpeg"
        assert llmchat.image_size(attachment.data) == (32, 24)

    def test_webp_size_is_unknown(self) -> None:
        data = b"RIFF" + (16).to_bytes(4, "little") + b"WEBPVP8 " + b"\x00" * 16
        assert llmchat.image_size(data) is None

    def test_fake_extension_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "a.png"
        path.write_bytes(b"PK\x03\x04not-an-image")
        with pytest.raises(ServeError, match="不支持的图片格式"):
            Attachment.image(path)

    def test_missing_file_reports_error(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="读取图片失败"):
            Attachment.image(tmp_path / "nope.png")

    def test_oversize_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "a.png"
        path.write_bytes(_png())
        with pytest.raises(ServeError, match="图片过大"):
            Attachment.image(path, max_bytes=8)

    def test_large_image_is_resized(self, tmp_path: Path) -> None:
        path = tmp_path / "a.png"
        path.write_bytes(_png(4000, 3000))
        calls: list[int] = []

        def resize(data: bytes, max_edge: int) -> bytes:
            calls.append(max_edge)
            return _png(512, 384)

        attachment = Attachment.image(path, max_edge=1568, resize=resize)
        assert calls == [1568]
        assert llmchat.image_size(attachment.data) == (512, 384)

    def test_resize_result_over_limit_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "a.png"
        path.write_bytes(_png(4000, 3000, padding=0))

        def resize(data: bytes, max_edge: int) -> bytes:
            return _png(4000, 3000, padding=200)

        with pytest.raises(ServeError, match="缩放后仍过大"):
            Attachment.image(path, max_bytes=64, resize=resize)

    def test_to_content_part(self) -> None:
        part = Attachment(kind="image", mime="image/png", size=3, data=b"abc").to_content_part()
        assert part["type"] == "image_url"
        assert part["image_url"]["url"].startswith("data:image/png;base64,")


class TestChatMessagePayload:
    def test_plain_message_keeps_string_content(self) -> None:
        assert ChatMessage("user", "hi").to_dict() == {"role": "user", "content": "hi"}

    def test_image_message_uses_content_array(self) -> None:
        attachment = Attachment(kind="image", mime="image/png", size=3, data=b"abc")
        payload = ChatMessage("user", "看图", (attachment,)).to_dict()
        assert payload["content"][0] == {"type": "text", "text": "看图"}
        assert payload["content"][1]["type"] == "image_url"

    def test_image_only_message_skips_text_part(self) -> None:
        attachment = Attachment(kind="image", mime="image/png", size=3, data=b"abc")
        payload = ChatMessage("user", "", (attachment,)).to_dict()
        assert len(payload["content"]) == 1
        assert payload["content"][0]["type"] == "image_url"

    def test_empty_prompt_without_attachment_is_rejected(self) -> None:
        session = ChatSession(endpoint=_endpoint())
        with pytest.raises(ServeError, match="提示词不能为空"):
            session.ask("   ")

    def test_ask_passes_attachments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        def fake_stream(endpoint: Any, messages: Any, **kwargs: Any) -> Any:
            seen["payload"] = [message.to_dict() for message in messages]
            yield ChatDelta(kind="content", text="ok")

        monkeypatch.setattr(llmchat, "stream_chat_events", fake_stream)
        session = ChatSession(endpoint=_endpoint())
        attachment = Attachment(kind="image", mime="image/png", size=3, data=b"abc")
        assert session.ask("", attachments=(attachment,)) == "ok"
        assert isinstance(seen["payload"][0]["content"], list)


# ---------------------------------------------------------------------- #
# 状态兼容与降级重试
# ---------------------------------------------------------------------- #
class TestStateCompat:
    def test_old_state_file_without_vision_fields(self, tmp_path: Path) -> None:
        path = llmenv.state_path("qwen", tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "name": "qwen",
            "pid": 1,
            "create_time": 1.0,
            "host": "127.0.0.1",
            "port": 8080,
            "model": "m.gguf",
            "profile": "balanced",
            "context": 8192,
            "engine": "b1",
            "alias": "qwen",
            "api_key": "k",
            "log": "log.txt",
            "started_at": "2026-01-01T00:00:00",
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = llmenv.load_state("qwen", tmp_path)
        assert loaded is not None
        assert loaded.mmproj == ""
        assert loaded.vision is False

    def test_new_state_round_trip(self, tmp_path: Path) -> None:
        llmenv.save_state(_state(tmp_path, mmproj="mmproj.gguf", vision=True), tmp_path)
        loaded = llmenv.load_state("qwen", tmp_path)
        assert loaded is not None
        assert loaded.mmproj == "mmproj.gguf"
        assert loaded.vision is True


class TestLaunchFallback:
    def test_retries_without_mmproj_on_projector_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        plan = _plan(tmp_path, mmproj=_mmproj(tmp_path))
        calls: list[LaunchPlan] = []
        expected = _state(tmp_path, pid=2, create_time=2.0)

        def fake_once(current: LaunchPlan, **kwargs: Any) -> ServeState:
            calls.append(current)
            if current.mmproj is not None:
                current.log.write_text("failed to load mmproj projector", encoding="utf-8")
                raise ServeError("boom")
            return expected

        monkeypatch.setattr(llmserve, "_launch_once", fake_once)
        assert llmserve.launch(plan) is expected
        assert len(calls) == 2
        assert calls[1].mmproj is None

    def test_no_retry_for_unrelated_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        plan = _plan(tmp_path, mmproj=_mmproj(tmp_path))
        calls: list[LaunchPlan] = []

        def fake_once(current: LaunchPlan, **kwargs: Any) -> ServeState:
            calls.append(current)
            current.log.write_text("port already in use", encoding="utf-8")
            raise ServeError("boom")

        monkeypatch.setattr(llmserve, "_launch_once", fake_once)
        with pytest.raises(ServeError):
            llmserve.launch(plan)
        assert len(calls) == 1

    def test_fallback_can_be_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        plan = _plan(tmp_path, mmproj=_mmproj(tmp_path))
        calls: list[LaunchPlan] = []

        def fake_once(current: LaunchPlan, **kwargs: Any) -> ServeState:
            calls.append(current)
            current.log.write_text("mmproj mismatch", encoding="utf-8")
            raise ServeError("boom")

        monkeypatch.setattr(llmserve, "_launch_once", fake_once)
        with pytest.raises(ServeError):
            llmserve.launch(plan, allow_mmproj_fallback=False)
        assert len(calls) == 1
