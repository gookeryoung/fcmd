"""llmpaint 命令与 sd.cpp 画图链路测试。

覆盖：
- 工具注册（9 个子命令）
- 资产识别 / 家族检测 / 默认组件 / 参数与载荷 / 校验 / 出图保存
- sd.cpp release 解析、变体选择、本地 zip / release 安装、marker 与切换
- 服务启动命令、状态读写、健康双探针、文生图客户端与停止
- CLI 的 models / gen / status / engine-* 行为
"""

from __future__ import annotations

import base64
import io
import json
import os
import zipfile
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from fcmd.apps.llm import _llmpaint, _llmpaint_engine, _llmpaint_server, llmengine, llmpaint
from fcmd.apps.llm._llmpaint_engine import SdEngine
from fcmd.apps.llm.llmbase import Check, CommandResult, GpuInfo, ServeError
from fcmd.core.toolkit import _TOOL_REGISTRY

FAMILY = _llmpaint.FAMILIES


# ---------------------------------------------------------------------- #
# 测试替身
# ---------------------------------------------------------------------- #
class _Resp:
    """最小 urllib 响应替身。"""

    def __init__(self, payload: bytes = b"", status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _opener(payload: bytes = b"", status: int = 200, seen: list[Request] | None = None) -> Any:
    def _open(request: Request, timeout: float = 2.0) -> _Resp:
        if seen is not None:
            seen.append(request)
        return _Resp(payload, status)

    return _open


def _sequence_opener(responses: list[tuple[int, bytes]], seen: list[Request] | None = None) -> Any:
    """按顺序返回多个（状态码, 响应体）。"""

    def _open(request: Request, timeout: float = 2.0) -> _Resp:
        if seen is not None:
            seen.append(request)
        status, payload = responses.pop(0)
        return _Resp(payload, status)

    return _open


def _payload_of(request: Request) -> dict[str, Any]:
    """请求体转 JSON（urllib 的 ``data`` 类型较宽，先收窄再解析）。"""
    data = request.data
    raw = data if isinstance(data, (bytes, bytearray)) else b"{}"
    return json.loads(raw)


def _weight(path: Path, size: int = 8) -> Path:
    """写入一个假权重文件（默认 8 字节，扫描时用 min_bytes=1）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    return path


def _sd_payload() -> list[Any]:
    """构造 stable-diffusion.cpp 的 GitHub releases 替身（含需过滤的条目）。"""

    def asset(name: str, size: int = 100) -> dict[str, Any]:
        return {"name": name, "browser_download_url": f"https://example.com/{name}", "size": size}

    return [
        {
            "tag_name": "master-1106-532d2c1",
            "published_at": "2026-01-02T03:04:05Z",
            "assets": [
                asset("sd-master-1106-532d2c1-bin-win-cuda12-x64.zip", 900),
                asset("sd-master-1106-532d2c1-bin-win-vulkan-x64.zip", 500),
                asset("sd-master-1106-532d2c1-bin-win-cpu-x64.zip", 300),
                asset("cudart-sd-bin-win-cu12-x64.zip", 50),
                asset("sd-master-1106-532d2c1-bin-win-cuda12-arm64.zip"),
                asset("sd-master-1106-532d2c1-bin-Linux-x64.zip"),
                asset("sd-master-1106-532d2c1-bin-win-cuda12-x64.zip.sha256"),
                {"name": "bad", "browser_download_url": None},
            ],
        },
        {
            "tag_name": "master-1105-aaaaaaa",
            "published_at": "2026-01-01T00:00:00Z",
            "assets": [asset("sd-master-1105-aaaaaaa-bin-Linux-x64.zip")],
        },
        "not-a-dict",
        {"tag_name": "master-1104-bbbbbbb", "assets": []},
        {
            "tag_name": "master-1103-ccccccc",
            "published_at": "",
            "assets": [asset("sd-master-1103-ccccccc-bin-win-cpu-x64.zip")],
        },
    ]


def _png_b64(text: bytes = b"png") -> str:
    return base64.b64encode(text).decode("ascii")


# ---------------------------------------------------------------------- #
# 工具注册
# ---------------------------------------------------------------------- #
def test_tool_registered() -> None:
    assert set(_TOOL_REGISTRY["llmpaint"]) == {
        "models",
        "gen",
        "serve",
        "stop",
        "status",
        "engine-list",
        "engine-install",
        "engine-use",
        "engine-check",
    }


# ---------------------------------------------------------------------- #
# 资产识别与家族
# ---------------------------------------------------------------------- #
class TestDetectFamily:
    @pytest.mark.parametrize(
        ("name", "family"),
        [
            ("sd15-pruned.safetensors", "sd15"),
            ("v1-5-pruned-emaonly.ckpt", "sd15"),
            ("sd21-base.safetensors", "sd2"),
            ("sdxl-base-1.0.safetensors", "sdxl"),
            ("ponyDiffusionV6.safetensors", "sdxl"),
            ("sd3-medium.safetensors", "sd3"),
            ("flux1-dev.safetensors", "flux"),
            ("FLUX.1-schnell.gguf", "flux"),
            ("qwen-image-edit.safetensors", "qwen-image"),
            ("qwen-image-2.1.safetensors", "qwen-image"),
            ("MiniMax-H3-Image-Q4_K_M.gguf", "minimax-h3"),
            ("z-image-turbo.safetensors", "z-image"),
            ("HiDream-I1-dev.safetensors", "hidream"),
            ("Kolors-v1.1.safetensors", "kolors"),
            ("qwen2.5-vl-7b-instruct.gguf", "unknown"),
            ("umt5-xxl-encoder.safetensors", "unknown"),
            ("random-weights.safetensors", "unknown"),
        ],
    )
    def test_family(self, name: str, family: str) -> None:
        assert _llmpaint.detect_family(Path(name)) == family

    def test_detect_family_uses_boundary(self) -> None:
        """``sd15`` 不应匹配 ``sd150`` 这类长 token。"""
        assert _llmpaint.detect_family(Path("sd150-model.safetensors")) == "unknown"

    @pytest.mark.parametrize(
        ("repo", "family"),
        [
            ("qwen-image-2.1", "qwen-image"),
            ("MiniMax-H3-Image", "minimax-h3"),
            ("HiDream-I1", "hidream"),
        ],
    )
    def test_detect_family_falls_back_to_repo_dir(self, repo: str, family: str) -> None:
        """HF/ModelScope 常见通用文件名（model.safetensors）要靠仓库目录识别家族。"""
        path = Path("models") / repo / "model.safetensors"
        assert _llmpaint.detect_family(path) == family
        assert _llmpaint.classify_role(path) == "diffusion"


class TestClassifyRole:
    @pytest.mark.parametrize(
        ("path", "role"),
        [
            (Path("models/vae/anything.safetensors"), "vae"),
            (Path("models/diffusion_models/flux-dev.safetensors"), "diffusion"),
            (Path("models/checkpoints/sd15.ckpt"), "checkpoint"),
            (Path("models/Stable-diffusion/model.safetensors"), "checkpoint"),
            (Path("models/text_encoders/t5xxl_fp16.safetensors"), "t5xxl"),
            (Path("models/text_encoders/umt5-xxl.safetensors"), "llm"),
            (Path("models/clip/clip_l.safetensors"), "clip_l"),
            (Path("models/clip/clip_g.safetensors"), "clip_g"),
            (Path("models/loras/detail.safetensors"), "lora"),
            (Path("models/clip_vision/vision.safetensors"), "unknown"),
            (Path("clip-l.safetensors"), "clip_l"),
            (Path("clip-g.safetensors"), "clip_g"),
            (Path("t5xxl_fp16.safetensors"), "t5xxl"),
            (Path("my-vae.safetensors"), "vae"),
            (Path("flux1-dev.safetensors"), "diffusion"),
            (Path("sdxl-base.safetensors"), "checkpoint"),
            (Path("models/qwen-image-2.1/model.safetensors"), "diffusion"),
            (Path("models/MiniMax-H3-Image/model-Q4_K_M.gguf"), "diffusion"),
            (Path("whatever.safetensors"), "unknown"),
        ],
    )
    def test_role(self, path: Path, role: str) -> None:
        assert _llmpaint.classify_role(path) == role

    def test_mmproj_skipped(self) -> None:
        assert _llmpaint.is_mmproj(Path("mmproj-vision.gguf")) is True
        assert _llmpaint.is_diffusion_weight(Path("mmproj-vision.gguf")) is False

    def test_is_diffusion_weight(self) -> None:
        assert _llmpaint.is_diffusion_weight(Path("flux1-dev.safetensors")) is True
        assert _llmpaint.is_diffusion_weight(Path("my-vae.safetensors")) is True
        assert _llmpaint.is_diffusion_weight(Path("qwen2.5-7b-instruct.gguf")) is False
        assert _llmpaint.is_diffusion_weight(Path("random-weights.safetensors")) is False


class TestScanAssets:
    def test_scan_groups_and_filters(self, tmp_path: Path) -> None:
        _weight(tmp_path / "checkpoints" / "sdxl-base.safetensors", 30)
        _weight(tmp_path / "checkpoints" / "small.safetensors", 1)
        _weight(tmp_path / "checkpoints" / "mmproj-vision.gguf", 10)
        _weight(tmp_path / "vae" / "sdxl-vae.safetensors", 10)
        _weight(tmp_path / "loras" / "detail.safetensors", 5)
        _weight(tmp_path / "notes.gguf", 5)  # 角色与家族都识别不了，忽略

        assets = _llmpaint.scan_assets([tmp_path, tmp_path], min_bytes=2)
        roles = [asset.role for asset in assets.assets]
        assert roles == ["checkpoint", "vae", "lora"]
        assert assets.main_models()[0].path.name == "sdxl-base.safetensors"
        assert [item.path.name for item in assets.by_role("vae")] == ["sdxl-vae.safetensors"]
        assert assets.roots == (tmp_path.resolve(),)

    def test_scan_missing_root(self, tmp_path: Path) -> None:
        assets = _llmpaint.scan_assets([tmp_path / "absent"])
        assert assets.assets == ()
        assert assets.roots == ((tmp_path / "absent").resolve(),)

    def test_get_by_path(self, tmp_path: Path) -> None:
        model = _weight(tmp_path / "checkpoints" / "sdxl.safetensors", 9)
        assets = _llmpaint.scan_assets([tmp_path], min_bytes=1)
        assert assets.get(model) is not None
        assert assets.get(tmp_path / "elsewhere.safetensors") is None
        assert assets.get(None) is None

    def test_describe_asset(self, tmp_path: Path) -> None:
        _weight(tmp_path / "checkpoints" / "sdxl.safetensors", 2048)
        asset = _llmpaint.scan_assets([tmp_path], min_bytes=1).assets[0]
        text = _llmpaint.describe_asset(asset)
        assert "SDXL" in text
        assert "Checkpoint" in text
        assert "KiB" in text or "KB" in text


class TestSuggestComponents:
    def test_sdxl_picks_vae(self, tmp_path: Path) -> None:
        model = _weight(tmp_path / "checkpoints" / "sdxl-base.safetensors", 20)
        vae = _weight(tmp_path / "vae" / "sdxl-vae.safetensors", 10)
        assets = _llmpaint.scan_assets([tmp_path], min_bytes=1)
        components = _llmpaint.suggest_components(assets)
        assert components.model == model.resolve()
        assert components.vae == vae.resolve()
        assert components.clip_l is None
        assert _llmpaint.effective_mode(components) == "checkpoint"

    def test_flux_picks_encoders(self, tmp_path: Path) -> None:
        _weight(tmp_path / "diffusion_models" / "flux1-dev.safetensors", 20)
        _weight(tmp_path / "text_encoders" / "clip_l.safetensors", 5)
        _weight(tmp_path / "text_encoders" / "t5xxl_fp16.safetensors", 5)
        _weight(tmp_path / "vae" / "ae.safetensors", 5)
        assets = _llmpaint.scan_assets([tmp_path], min_bytes=1)
        components = _llmpaint.suggest_components(assets)
        assert components.model is not None
        assert components.clip_l is not None
        assert components.t5xxl is not None
        assert components.vae is not None
        assert _llmpaint.effective_mode(components) == "diffusion"

    def test_explicit_model_and_empty(self, tmp_path: Path) -> None:
        model = _weight(tmp_path / "checkpoints" / "sdxl.safetensors", 4)
        assert _llmpaint.suggest_components(_llmpaint.PaintAssets(), None).model is None
        components = _llmpaint.suggest_components(_llmpaint.PaintAssets(), model)
        assert components.model == model

    def test_unknown_family_slots(self, tmp_path: Path) -> None:
        model = _weight(tmp_path / "checkpoints" / "custom.safetensors", 4)
        assert _llmpaint.detect_family(model) == "unknown"
        assert FAMILY["unknown"].mode == "checkpoint"


# ---------------------------------------------------------------------- #
# 参数、载荷与校验
# ---------------------------------------------------------------------- #
class TestBuildArgs:
    def test_checkpoint(self) -> None:
        components = _llmpaint.PaintComponents(
            model=Path("model.safetensors"),
            vae=Path("vae.safetensors"),
            extra_args=("--foo", "bar"),
        )
        assert _llmpaint.build_model_args(components) == [
            "-m",
            "model.safetensors",
            "--vae",
            "vae.safetensors",
            "--foo",
            "bar",
        ]

    def test_diffusion_with_encoders_and_flags(self) -> None:
        components = _llmpaint.PaintComponents(
            model=Path("flux.safetensors"),
            mode="diffusion",
            clip_l=Path("clip_l.safetensors"),
            clip_g=Path("clip_g.safetensors"),
            t5xxl=Path("t5xxl.safetensors"),
            llm=Path("llm.gguf"),
            offload_to_cpu=True,
            vae_tiling=True,
            diffusion_fa=True,
        )
        args = _llmpaint.build_model_args(components)
        assert args[:4] == ["--diffusion-model", "flux.safetensors", "--clip_l", "clip_l.safetensors"]
        assert args[-3:] == ["--offload-to-cpu", "--vae-tiling", "--diffusion-fa"]

    def test_no_model(self) -> None:
        assert _llmpaint.build_model_args(_llmpaint.PaintComponents()) == []

    def test_server_flags(self) -> None:
        assert _llmpaint.server_flags(_llmpaint.PaintComponents()) == []
        assert _llmpaint.server_flags(_llmpaint.PaintComponents(vae_tiling=True)) == ["--vae-tiling"]


class TestPayload:
    def test_payload_includes_sampler(self) -> None:
        request = _llmpaint.PaintRequest(prompt="a cat", negative="blurry", width=768, height=512, steps=20, batch=2)
        payload = _llmpaint.build_payload(request)
        assert payload["prompt"] == "a cat"
        assert payload["negative_prompt"] == "blurry"
        assert payload["width"] == 768
        assert payload["batch_size"] == 2
        assert payload["n_iter"] == 1
        assert payload["sampler_name"] == "euler_a"
        assert "scheduler" not in payload

    def test_payload_with_scheduler(self) -> None:
        payload = _llmpaint.build_payload(_llmpaint.PaintRequest(prompt="x", sampler="", scheduler="karras"))
        assert "sampler_name" not in payload
        assert payload["scheduler"] == "karras"


class TestValidate:
    def test_request_ok(self) -> None:
        _llmpaint.validate_request(_llmpaint.PaintRequest(prompt="x", width=1024, height=1024, steps=150, cfg_scale=0))

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"prompt": "  "}, "提示词"),
            ({"prompt": "x", "width": 63}, "宽度"),
            ({"prompt": "x", "height": 500}, "高度"),
            ({"prompt": "x", "width": 4104}, "宽度"),
            ({"prompt": "x", "steps": 0}, "步数"),
            ({"prompt": "x", "steps": 151}, "步数"),
            ({"prompt": "x", "cfg_scale": -1}, "CFG"),
            ({"prompt": "x", "cfg_scale": 31}, "CFG"),
            ({"prompt": "x", "seed": -2}, "种子"),
            ({"prompt": "x", "batch": 0}, "批量"),
            ({"prompt": "x", "batch": 9}, "批量"),
        ],
    )
    def test_request_invalid(self, kwargs: dict[str, Any], message: str) -> None:
        request = _llmpaint.PaintRequest(**kwargs)
        with pytest.raises(ServeError, match=message):
            _llmpaint.validate_request(request)

    def test_components_ok(self, tmp_path: Path) -> None:
        model = _weight(tmp_path / "sdxl.safetensors", 4)
        _llmpaint.validate_components(_llmpaint.PaintComponents(model=model))

    def test_components_invalid(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="主模型"):
            _llmpaint.validate_components(_llmpaint.PaintComponents())
        with pytest.raises(ServeError, match="不存在"):
            _llmpaint.validate_components(_llmpaint.PaintComponents(model=tmp_path / "absent.gguf"))
        model = _weight(tmp_path / "sdxl.safetensors", 4)
        with pytest.raises(ServeError, match="加载方式"):
            _llmpaint.validate_components(_llmpaint.PaintComponents(model=model, mode="bogus"))
        with pytest.raises(ServeError, match="VAE"):
            _llmpaint.validate_components(
                _llmpaint.PaintComponents(model=model, vae=tmp_path / "absent-vae.safetensors")
            )

    def test_effective_mode(self, tmp_path: Path) -> None:
        assert _llmpaint.effective_mode(_llmpaint.PaintComponents()) == "checkpoint"
        model = _weight(tmp_path / "flux1-dev.safetensors", 4)
        assert _llmpaint.effective_mode(_llmpaint.PaintComponents(model=model)) == "diffusion"
        assert _llmpaint.effective_mode(_llmpaint.PaintComponents(model=model, mode="checkpoint")) == "checkpoint"


# ---------------------------------------------------------------------- #
# 图片解码 / 保存 / 图库
# ---------------------------------------------------------------------- #
class TestImages:
    def test_decode_bare_and_data_url(self) -> None:
        assert _llmpaint.decode_image(_png_b64(b"abc")) == b"abc"
        assert _llmpaint.decode_image(f"data:image/png;base64,{_png_b64(b'abc')}") == b"abc"
        assert _llmpaint.decode_image(" YWJj \n") == b"abc"

    def test_decode_invalid(self) -> None:
        with pytest.raises(ServeError, match="base64"):
            _llmpaint.decode_image("!!!not-base64!!!")

    def test_save_images(self, tmp_path: Path) -> None:
        paths = _llmpaint.save_images(
            [_png_b64(b"a"), f"data:image/png;base64,{_png_b64(b'b')}"],
            tmp_path,
            seed=-1,
        )
        assert len(paths) == 2
        assert all(path.name.startswith("paint-") and path.suffix == ".png" for path in paths)
        assert paths[0].read_bytes() == b"a"
        assert paths[1].read_bytes() == b"b"
        assert "-s-" not in paths[0].name and not paths[0].stem.endswith("-s")

    def test_save_images_seed_and_unique(self, tmp_path: Path) -> None:
        first = _llmpaint.save_images([_png_b64(b"a")], tmp_path, seed=42)[0]
        second = _llmpaint.save_images([_png_b64(b"a")], tmp_path, seed=42)[0]
        assert first.stem.endswith("-s42")
        assert second.stem.endswith("-2")
        assert second != first

    def test_save_images_empty(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="没有返回图片"):
            _llmpaint.save_images([], tmp_path)

    def test_gallery_order_and_limit(self, tmp_path: Path) -> None:
        for index in range(3):
            path = tmp_path / f"paint-2026010{index}-000000-00.png"
            path.write_bytes(b"x")
            path.touch()

        stamps = [(tmp_path / f"paint-2026010{index}-000000-00.png", 1000 + index) for index in range(3)]
        for path, stamp in stamps:
            os.utime(path, (stamp, stamp))
        (tmp_path / "other.png").write_bytes(b"x")
        items = _llmpaint.gallery(tmp_path, limit=2)
        assert [item.name for item in items] == ["paint-20260102-000000-00.png", "paint-20260101-000000-00.png"]
        assert _llmpaint.gallery(tmp_path / "absent") == ()


# ---------------------------------------------------------------------- #
# stable-diffusion.cpp 引擎
# ---------------------------------------------------------------------- #
class TestSdReleases:
    def test_parse_filters_windows_assets(self) -> None:
        releases = _llmpaint_engine.parse_sd_releases(_sd_payload())
        assert [item.tag for item in releases] == ["master-1106-532d2c1", "master-1103-ccccccc"]
        first = releases[0]
        assert first.published.startswith("2026-01-02")
        assert first.variants() == ("cuda12", "vulkan", "cpu")
        cuda = first.install_assets("cuda12")
        assert [asset.name for asset in cuda] == [
            "sd-master-1106-532d2c1-bin-win-cuda12-x64.zip",
            "cudart-sd-bin-win-cu12-x64.zip",
        ]
        assert [asset.runtime for asset in cuda] == [False, True]
        assert [asset.name for asset in first.install_assets("vulkan")] == [
            "sd-master-1106-532d2c1-bin-win-vulkan-x64.zip"
        ]
        assert releases[1].published == ""

    def test_parse_limit_and_invalid_payload(self) -> None:
        assert len(_llmpaint_engine.parse_sd_releases(_sd_payload(), limit=1)) == 1
        assert _llmpaint_engine.parse_sd_releases("not-a-list") == ()

    def test_fetch_reads_github_api(self) -> None:
        body = json.dumps(_sd_payload()).encode("utf-8")
        seen: list[Request] = []
        releases = _llmpaint_engine.fetch_sd_releases(limit=5, opener=_opener(body, 200, seen))
        assert releases[0].tag == "master-1106-532d2c1"
        assert seen and "per_page=10" in seen[0].full_url

    def test_fetch_network_error(self) -> None:
        def boom(request: Request, timeout: float = 1.0) -> Any:
            raise OSError("offline")

        with pytest.raises(ServeError, match="获取画图引擎版本列表失败"):
            _llmpaint_engine.fetch_sd_releases(opener=boom)

    def test_default_variant(self) -> None:
        assert _llmpaint_engine.pick_default_variant(()) is None
        assert _llmpaint_engine.pick_default_variant(["cpu", "vulkan"]) == "vulkan"
        assert _llmpaint_engine.pick_default_variant(["cpu"]) == "cpu"
        gpu = GpuInfo("RTX", 8192)
        assert _llmpaint_engine.pick_default_variant(["cuda12", "vulkan"], gpu) == "cuda12"
        assert _llmpaint_engine.pick_default_variant(["vulkan"], gpu) == "vulkan"
        assert _llmpaint_engine.pick_default_variant(["rocm-10.1", "cpu"]) == "cpu"

    def test_variant_label(self) -> None:
        assert _llmpaint_engine.variant_label("cuda12") == "CUDA 12"
        assert _llmpaint_engine.variant_label("vulkan") == "Vulkan"
        assert _llmpaint_engine.variant_label("cpu") == "CPU"
        assert _llmpaint_engine.variant_label("rocm-10.1") == "ROCm 10.1"
        assert _llmpaint_engine.variant_label("rocm") == "ROCm"
        assert _llmpaint_engine.variant_label("other") == "other"


class TestInstallEngine:
    def test_install_from_zip(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        archive = _make_zip(tmp_path / "bundle.zip", {"sd-server.exe": b""})
        engine = _llmpaint_engine.install_sd_engine("sd-test", archives=[archive], home=home, use=True)
        assert engine.exe.name == "sd-server.exe"
        assert engine.exe.is_file()
        assert (home / "engines" / "sd.cpp" / "sd-test" / "engine.json").is_file()
        assert _llmpaint_engine.current_sd_tag(home) == "sd-test"
        infos = _llmpaint_engine.list_sd_engines(home)
        assert [(item.tag, item.source) for item in infos] == [("sd-test", "current")]
        assert _llmpaint_engine.resolve_sd_engine(home=home).tag == "sd-test"

    def test_register_directory(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        source = tmp_path / "compiled"
        source.mkdir()
        (source / "sd-server.exe").write_bytes(b"exe")
        engine = _llmpaint_engine.install_sd_engine("local", path=source, home=home)
        assert engine.source == str(source)
        assert engine.exe == source / "sd-server.exe"
        assert (home / "engines" / "sd.cpp" / "local" / "engine.json").is_file()

    def test_install_rejects_bad_archives(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.zip"
        broken.write_bytes(b"not a zip")
        with pytest.raises(ServeError, match="离线包无效"):
            _llmpaint_engine.install_sd_engine("sd-test", archives=[broken], home=tmp_path / "home")
        empty = _make_zip(tmp_path / "empty.zip", {"README.md": b"x"})
        with pytest.raises(ServeError, match="找不到 sd-server"):
            _llmpaint_engine.install_sd_engine("sd-test", archives=[empty], home=tmp_path / "home")

    def test_install_rejects_bad_tag(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="不合法"):
            _llmpaint_engine.install_sd_engine("bad tag", archives=[], home=tmp_path)

    def test_install_from_release(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        home = tmp_path / "home"
        release = _llmpaint_engine.parse_sd_releases(_sd_payload())[0]
        monkeypatch.setattr(_llmpaint_engine, "fetch_sd_releases", lambda limit=10, opener=None: (release,))
        downloads: list[Path] = []

        def fake_download(url: str, dest: Path, opener: Any = None, on_progress: Any = None) -> int:
            downloads.append(dest)
            _make_zip(dest, {"sd-server.exe": b"", "ggml.dll": b""})
            return dest.stat().st_size

        monkeypatch.setattr(llmengine, "download_file", fake_download)
        engine = _llmpaint_engine.install_sd_engine(release.tag, variant="cuda12", home=home, use=True)
        assert engine.backend == "cuda12"
        assert [path.name for path in downloads] == [
            "sd-master-1106-532d2c1-bin-win-cuda12-x64.zip",
            "cudart-sd-bin-win-cu12-x64.zip",
        ]
        assert _llmpaint_engine.current_sd_tag(home) == release.tag

    def test_install_release_missing_variant(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        release = _llmpaint_engine.parse_sd_releases(_sd_payload())[0]
        monkeypatch.setattr(_llmpaint_engine, "fetch_sd_releases", lambda limit=10, opener=None: (release,))
        with pytest.raises(ServeError, match="没有变体"):
            _llmpaint_engine.install_sd_engine(release.tag, variant="rocm-7.0", home=tmp_path)

    def test_install_by_url(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        def fake_download(url: str, dest: Path, opener: Any = None, on_progress: Any = None) -> int:
            _make_zip(dest, {"sd-server.exe": b""})
            return dest.stat().st_size

        monkeypatch.setattr(llmengine, "download_file", fake_download)
        engine = _llmpaint_engine.install_sd_engine(
            "custom", url="https://example.com/sd-custom.zip", home=tmp_path / "home"
        )
        assert engine.tag == "custom"
        assert engine.backend == "custom"

    def test_install_conflicting_sources(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="不能同时"):
            _llmpaint_engine.install_sd_engine(
                "x", path=tmp_path, archives=[tmp_path / "a.zip"], home=tmp_path / "home"
            )

    def test_use_and_resolve_errors(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        with pytest.raises(ServeError, match="未安装"):
            _llmpaint_engine.use_sd_engine("absent", home)
        with pytest.raises(ServeError, match="未安装"):
            _llmpaint_engine.resolve_sd_engine("absent", home)
        with pytest.raises(ServeError, match="未找到"):
            _llmpaint_engine.resolve_sd_engine(home=home)

    def test_resolve_env_and_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        exe = tmp_path / "sd-server.exe"
        exe.write_bytes(b"")
        monkeypatch.setenv("FCMD_SD_SERVER", str(exe))
        engine = _llmpaint_engine.resolve_sd_engine(home=tmp_path)
        assert engine.tag == "env"
        assert engine.exe == exe

        monkeypatch.delenv("FCMD_SD_SERVER")
        monkeypatch.setattr(_llmpaint_engine.shutil, "which", lambda name: str(exe))
        engine = _llmpaint_engine.resolve_sd_engine(home=tmp_path / "empty")
        assert engine.tag == "system"

        monkeypatch.setenv("FCMD_SD_SERVER", str(tmp_path / "absent.exe"))
        with pytest.raises(ServeError, match="FCMD_SD_SERVER"):
            _llmpaint_engine.resolve_sd_engine(home=tmp_path)

    def test_engine_env_and_check(self, tmp_path: Path) -> None:
        assert _llmpaint_engine.sd_engine_env(SdEngine("t", Path("sd-server.exe"))) is None
        engine = SdEngine("t", Path("sd-server.exe"), path_prepend=(str(tmp_path),))
        env = _llmpaint_engine.sd_engine_env(engine)
        assert env is not None
        assert env["PATH"].split(os.pathsep)[0] == str(tmp_path)

        ok = _llmpaint_engine.check_sd_engine(engine, runner=lambda argv, **kw: CommandResult(0, "usage: sd-server"))
        assert ok == Check("画图引擎", "ok", ok.detail)
        assert ok.status == "ok"
        bad = _llmpaint_engine.check_sd_engine(engine, runner=lambda argv, **kw: CommandResult(1, "", "boom"))
        assert bad.status == "fail"
        assert "boom" in bad.detail


# ---------------------------------------------------------------------- #
# 服务与客户端
# ---------------------------------------------------------------------- #
def _state(tmp_path: Path, **overrides: Any) -> _llmpaint_server.PaintState:
    data: dict[str, Any] = {
        "pid": 4321,
        "create_time": 1.5,
        "host": "127.0.0.1",
        "port": 1234,
        "engine_tag": "sd-test",
        "exe": str(tmp_path / "sd-server.exe"),
        "model": str(tmp_path / "model.safetensors"),
        "components": {"vae": str(tmp_path / "vae.safetensors")},
        "fingerprint": ("sd-test", "-m"),
        "log": str(tmp_path / "sd.log"),
        "started_at": "2026-01-01T00:00:00Z",
    }
    data.update(overrides)
    return _llmpaint_server.PaintState(**data)


def _plan(tmp_path: Path, *, port: int = 1234) -> _llmpaint_server.PaintPlan:
    engine = SdEngine("sd-test", tmp_path / "sd-server.exe")
    components = _llmpaint.PaintComponents(model=tmp_path / "model.safetensors")
    return _llmpaint_server.PaintPlan(engine, components, tmp_path / "sd.log", port=port)


class TestPlan:
    def test_plan_argv(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path)
        assert _llmpaint_server.plan_argv(plan) == [
            str(tmp_path / "sd-server.exe"),
            "--listen-ip",
            "127.0.0.1",
            "--listen-port",
            "1234",
            "-m",
            str(tmp_path / "model.safetensors"),
        ]

    def test_make_plan(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        engine = SdEngine("sd-test", tmp_path / "sd-server.exe")
        monkeypatch.setattr(_llmpaint_server, "resolve_sd_engine", lambda tag=None, home=None: engine)
        plan = _llmpaint_server.make_paint_plan(_llmpaint.PaintComponents(), home=tmp_path, port=5678)
        assert plan.engine is engine
        assert plan.host == "127.0.0.1"
        assert plan.port == 5678
        assert plan.log == _llmpaint_server.log_path(tmp_path)
        with pytest.raises(ServeError, match="端口"):
            _llmpaint_server.make_paint_plan(_llmpaint.PaintComponents(), home=tmp_path, port=0)

    def test_fingerprint_changes(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path)
        other = _llmpaint_server.PaintPlan(
            plan.engine,
            _llmpaint.PaintComponents(model=plan.components.model, vae=tmp_path / "v.safetensors"),
            plan.log,
        )
        assert _llmpaint_server.fingerprint(plan) != _llmpaint_server.fingerprint(other)
        extra = _llmpaint_server.PaintPlan(
            plan.engine,
            _llmpaint.PaintComponents(model=plan.components.model, vae_tiling=True),
            plan.log,
        )
        assert _llmpaint_server.fingerprint(plan) != _llmpaint_server.fingerprint(extra)


class TestState:
    def test_roundtrip(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        path = _llmpaint_server.save_state(state, tmp_path)
        assert path == _llmpaint_server.state_path(tmp_path)
        assert _llmpaint_server.load_state(tmp_path) == state
        _llmpaint_server.delete_state(tmp_path)
        assert _llmpaint_server.load_state(tmp_path) is None

    def test_corrupt_and_missing(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="不存在"):
            _llmpaint_server.load_state(tmp_path, missing_ok=False)
        path = _llmpaint_server.state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="utf-8")
        assert _llmpaint_server.load_state(tmp_path) is None
        with pytest.raises(ServeError, match="损坏"):
            _llmpaint_server.load_state(tmp_path, missing_ok=False)
        path.write_text(json.dumps({"pid": "bad"}), encoding="utf-8")
        assert _llmpaint_server.load_state(tmp_path) is None

    def test_status(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        state = _state(tmp_path)
        _llmpaint_server.save_state(state, tmp_path)
        monkeypatch.setattr(_llmpaint_server.llmserve, "process_alive", lambda pid, create_time=0.0: True)
        assert _llmpaint_server.status(tmp_path) == (state, True)
        assert _llmpaint_server.status(tmp_path / "empty") == (None, False)


class TestHealth:
    def test_health_first_probe(self) -> None:
        assert _llmpaint_server.check_paint_health("127.0.0.1", 1234, _opener(b"", 200)) is True

    def test_health_falls_back_to_models(self) -> None:
        seen: list[str] = []

        def opener(request: Request, timeout: float = 2.0) -> _Resp:
            seen.append(request.full_url)
            return _Resp(b"", 404 if request.full_url.endswith("/health") else 200)

        assert _llmpaint_server.check_paint_health("127.0.0.1", 1234, opener) is True
        assert any(url.endswith("/sdapi/v1/sd-models") for url in seen)

    def test_health_all_fail(self) -> None:
        assert _llmpaint_server.check_paint_health("127.0.0.1", 1234, _opener(b"", 500)) is False


class TestTxt2Img:
    def test_success(self) -> None:
        seen: list[Request] = []
        body = json.dumps({"images": [_png_b64(b"png")]}).encode("utf-8")
        images = _llmpaint_server.txt2img("127.0.0.1", 1234, {"prompt": "x"}, opener=_opener(body, 200, seen))
        assert images == [_png_b64(b"png")]
        assert seen[0].method == "POST"
        assert _payload_of(seen[0]) == {"prompt": "x"}

    def test_retries_without_sampler(self) -> None:
        seen: list[Request] = []
        responses = [
            (400, b'{"detail":"unknown field sampler_name"}'),
            (200, json.dumps({"images": [_png_b64(b"ok")]}).encode("utf-8")),
        ]
        images = _llmpaint_server.txt2img(
            "127.0.0.1",
            1234,
            {"prompt": "x", "sampler_name": "euler_a", "scheduler": "karras"},
            opener=_sequence_opener(responses, seen),
        )
        assert images == [_png_b64(b"ok")]
        assert len(seen) == 2
        assert _payload_of(seen[1]) == {"prompt": "x"}

    def test_http_error(self) -> None:
        with pytest.raises(ServeError, match="HTTP 500"):
            _llmpaint_server.txt2img("127.0.0.1", 1234, {"prompt": "x"}, opener=_opener(b"boom", 500))

    def test_http_error_response_object(self) -> None:
        def opener(request: Request, timeout: float = 2.0) -> Any:
            raise HTTPError(request.full_url, 400, "bad", Message(), io.BytesIO(b"invalid request"))

        with pytest.raises(ServeError, match="HTTP 400"):
            _llmpaint_server.txt2img("127.0.0.1", 1234, {"prompt": "x"}, opener=opener)

    def test_connection_failure(self) -> None:
        def opener(request: Request, timeout: float = 2.0) -> Any:
            raise OSError("refused")

        with pytest.raises(ServeError, match="请求失败"):
            _llmpaint_server.txt2img("127.0.0.1", 1234, {}, opener=opener)

    def test_invalid_json_and_empty_images(self) -> None:
        with pytest.raises(ServeError, match="不是 JSON"):
            _llmpaint_server.txt2img("127.0.0.1", 1234, {}, opener=_opener(b"<html>", 200))
        with pytest.raises(ServeError, match="没有返回 images"):
            _llmpaint_server.txt2img("127.0.0.1", 1234, {}, opener=_opener(b"{}", 200))
        with pytest.raises(ServeError, match="images 为空"):
            _llmpaint_server.txt2img("127.0.0.1", 1234, {}, opener=_opener(b'{"images": [""]}', 200))

    def test_progress_and_interrupt(self) -> None:
        body = json.dumps({"progress": 0.5}).encode("utf-8")
        assert _llmpaint_server.fetch_progress("127.0.0.1", 1234, _opener(body)) == {"progress": 0.5}
        assert _llmpaint_server.fetch_progress("127.0.0.1", 1234, _opener(b"", 404)) is None
        assert _llmpaint_server.fetch_progress("127.0.0.1", 1234, _opener(b"not json")) is None
        assert _llmpaint_server.progress_value({"progress": 0.4}) == 0.4
        assert _llmpaint_server.progress_value({"percent": 40}) == 0.4
        assert _llmpaint_server.progress_value({"progress": "x"}) is None
        assert _llmpaint_server.progress_value({"progress": True}) is None
        assert _llmpaint_server.progress_value(None) is None
        assert _llmpaint_server.interrupt("127.0.0.1", 1234, _opener(b"", 200)) is True
        assert _llmpaint_server.interrupt("127.0.0.1", 1234, _opener(b"", 404)) is False


class TestLifecycle:
    def test_stop_without_state(self, tmp_path: Path) -> None:
        result = _llmpaint_server.stop_paint(tmp_path)
        assert result.state is None
        assert result.stopped is False

    def test_stop_cleans_dead_state(self, tmp_path: Path) -> None:
        _llmpaint_server.save_state(_state(tmp_path), tmp_path)
        result = _llmpaint_server.stop_paint(tmp_path, alive=lambda pid, create_time: False)
        assert result.state is not None
        assert result.stopped is False
        assert _llmpaint_server.load_state(tmp_path) is None

    def test_stop_terminates_and_cleans(self, tmp_path: Path) -> None:
        _llmpaint_server.save_state(_state(tmp_path), tmp_path)
        calls: list[tuple[int, float, float]] = []
        result = _llmpaint_server.stop_paint(
            tmp_path,
            alive=lambda pid, create_time: True,
            terminator=lambda pid, create_time, timeout: calls.append((pid, create_time, timeout)),
        )
        assert result.stopped is True
        assert calls == [(4321, 1.5, 10.0)]
        assert _llmpaint_server.load_state(tmp_path) is None

    def test_start_rejects_running_service(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _llmpaint_server.save_state(_state(tmp_path), tmp_path)
        monkeypatch.setattr(_llmpaint_server.llmserve, "process_alive", lambda pid, create_time=0.0: True)
        with pytest.raises(ServeError, match="已在运行"):
            _llmpaint_server.start_paint(_plan(tmp_path), home=tmp_path)

    def test_start_rejects_busy_port(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="端口"):
            _llmpaint_server.start_paint(_plan(tmp_path), home=tmp_path, port_in_use=lambda port, host: True)

    def test_launch_waits_for_health(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        class _Proc:
            pid = 424242
            returncode: int | None = None

            def poll(self) -> None:
                return None

        monkeypatch.setattr(_llmpaint_server.llmserve, "process_create_time", lambda pid: 1.0)
        state = _llmpaint_server.launch_paint(
            _plan(tmp_path),
            home=tmp_path,
            popen=lambda argv, **kwargs: _Proc(),
            health=lambda host, port: True,
        )
        assert state.pid == 424242
        assert state.create_time == 1.0
        assert state.fingerprint == _llmpaint_server.fingerprint(_plan(tmp_path))
        assert _llmpaint_server.load_state(tmp_path) == state

    def test_launch_detects_early_exit(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        class _Proc:
            pid = 424243
            returncode = 3

            def poll(self) -> int:
                return 3

        monkeypatch.setattr(_llmpaint_server.llmserve, "process_create_time", lambda pid: 1.0)
        plan = _plan(tmp_path)
        plan.log.write_text("boom", encoding="utf-8")
        with pytest.raises(ServeError, match="立即退出"):
            _llmpaint_server.launch_paint(
                plan, home=tmp_path, popen=lambda argv, **kwargs: _Proc(), health=lambda host, port: False
            )

    def test_launch_timeout_terminates(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        class _Proc:
            pid = 424244
            returncode: int | None = None

            def poll(self) -> None:
                return None

        terminated: list[tuple[int, float, float]] = []
        monkeypatch.setattr(_llmpaint_server.llmserve, "process_create_time", lambda pid: 1.0)
        monkeypatch.setattr(
            _llmpaint_server.llmserve,
            "terminate_process",
            lambda pid, create_time, timeout: terminated.append((pid, create_time, timeout)),
        )
        plan = _plan(tmp_path)
        plan = _llmpaint_server.PaintPlan(plan.engine, plan.components, plan.log, wait_s=0.0)
        with pytest.raises(ServeError, match="超时"):
            _llmpaint_server.launch_paint(
                plan, home=tmp_path, popen=lambda argv, **kwargs: _Proc(), health=lambda host, port: False
            )
        assert terminated and terminated[0][0] == 424244


# ---------------------------------------------------------------------- #
# CLI 子命令
# ---------------------------------------------------------------------- #
def _assets(tmp_path: Path, count: int = 1, size: int = 4) -> _llmpaint.PaintAssets:
    for index in range(count):
        _weight(tmp_path / "checkpoints" / f"sdxl-base-{index}.safetensors", size)
    return _llmpaint.scan_assets([tmp_path], min_bytes=1)


_BIG = _llmpaint.MIN_WEIGHT_BYTES + 1


class TestModelsCommand:
    def test_empty(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(llmpaint, "_scan", lambda directory=None: _llmpaint.PaintAssets((), (tmp_path,)))
        llmpaint.models_cmd()
        out = capsys.readouterr().out
        assert "没有找到扩散模型" in out
        assert str(tmp_path) in out

    def test_table(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _assets(tmp_path, size=_BIG)
        llmpaint.models_cmd(directory=tmp_path)
        out = capsys.readouterr().out
        assert "本地扩散模型与组件" in out
        assert "Checkpoint" in out
        assert "SDXL" in out

    def test_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _assets(tmp_path, size=_BIG)
        llmpaint.models_cmd(directory=tmp_path, json=True)
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["role"] == "checkpoint"
        assert payload[0]["family"] == "sdxl"
        assert payload[0]["path"].endswith("sdxl-base-0.safetensors")


class TestGenCommand:
    def test_requires_model(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(llmpaint, "_scan", lambda directory=None: _llmpaint.PaintAssets())
        with pytest.raises(ServeError, match="--model"):
            llmpaint.gen_cmd(prompt="a cat")

    def test_multiple_candidates(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        assets = _assets(tmp_path, count=2)
        monkeypatch.setattr(llmpaint, "_scan", lambda directory=None: assets)
        with pytest.raises(ServeError, match="sdxl-base-0"):
            llmpaint.gen_cmd(prompt="a cat")

    def test_generates_and_saves(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assets = _assets(tmp_path)
        monkeypatch.setattr(llmpaint, "_scan", lambda directory=None: assets)
        monkeypatch.setattr(llmpaint, "_ensure_running", lambda plan, no_start: object())
        monkeypatch.setattr(
            llmpaint,
            "_llmpaint_server",
            _stub_server(
                make_paint_plan=lambda components, port=1234, engine_tag=None, wait_s=300.0: _plan(tmp_path),
                txt2img=lambda host, port, payload: [_png_b64(b"png")],
            ),
        )
        out = tmp_path / "out"
        llmpaint.gen_cmd(prompt="a cat", model=assets.main_models()[0].path, out=out)
        saved = list(out.glob("paint-*.png"))
        assert len(saved) == 1
        assert saved[0].read_bytes() == b"png"
        assert "已生成 1 张图片" in capsys.readouterr().out

    def test_no_start_requires_running_service(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path)
        with pytest.raises(ServeError, match="--no-start"):
            llmpaint._ensure_running(plan, no_start=True)

    def test_pick_model_prefers_explicit(self, tmp_path: Path) -> None:
        assets = _assets(tmp_path)
        explicit = tmp_path / "other.safetensors"
        assert llmpaint._pick_model(explicit, assets) == explicit.expanduser()


class TestServeAndStatus:
    def test_serve_starts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assets = _assets(tmp_path)
        monkeypatch.setattr(llmpaint, "_scan", lambda directory=None: assets)
        monkeypatch.setattr(
            llmpaint,
            "_llmpaint_server",
            _stub_server(
                make_paint_plan=lambda components, port=1234, engine_tag=None, wait_s=300.0: _plan(tmp_path),
                start_paint=lambda plan: _state(tmp_path),
            ),
        )
        llmpaint.serve_cmd()
        out = capsys.readouterr().out
        assert "画图服务就绪" in out
        assert "sd-test" in out

    def test_status_text_and_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state = _state(tmp_path)
        monkeypatch.setattr(llmpaint, "_llmpaint_server", _stub_server(status=lambda: (state, True)))
        llmpaint.status_cmd()
        out = capsys.readouterr().out
        assert "运行中" in out
        assert state.base_url in out

        llmpaint.status_cmd(json=True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["running"] is True
        assert payload["pid"] == 4321
        assert payload["engine"] == "sd-test"

    def test_status_none(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(llmpaint, "_llmpaint_server", _stub_server(status=lambda: (None, False)))
        llmpaint.status_cmd()
        assert "未运行" in capsys.readouterr().out

    def test_stop_variants(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state = _state(tmp_path)
        results = [
            _llmpaint_server.PaintStopResult(None, False),
            _llmpaint_server.PaintStopResult(state, True),
            _llmpaint_server.PaintStopResult(state, False),
        ]
        monkeypatch.setattr(llmpaint, "_llmpaint_server", _stub_server(stop_paint=lambda home=None: results.pop(0)))
        llmpaint.stop_cmd()
        assert "未运行" in capsys.readouterr().out
        llmpaint.stop_cmd()
        assert "已停止" in capsys.readouterr().out
        llmpaint.stop_cmd()
        assert "清理残留状态" in capsys.readouterr().out


class TestEngineCommands:
    def test_list_empty(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(llmpaint._llmpaint_engine, "list_sd_engines", lambda home=None: [])
        llmpaint.engine_list_cmd()
        assert "未安装画图引擎" in capsys.readouterr().out

    def test_list_marks_current(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        engine = SdEngine("master-1", Path("sd-server.exe"), "current", "cuda12")
        monkeypatch.setattr(llmpaint._llmpaint_engine, "list_sd_engines", lambda home=None: [engine])
        llmpaint.engine_list_cmd()
        out = capsys.readouterr().out
        assert "master-1" in out
        assert "cuda12" in out

    def test_install_uses_engine_module(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        seen: dict[str, Any] = {}

        def fake_install(tag: str | None = None, **kwargs: Any) -> SdEngine:
            seen.update({"tag": tag, **kwargs})
            return SdEngine(tag or "x", tmp_path / "sd-server.exe")

        monkeypatch.setattr(llmpaint._llmpaint_engine, "install_sd_engine", fake_install)
        archive = tmp_path / "bundle.zip"
        llmpaint.engine_install_cmd(tag="master-2", variant="vulkan", archives=[archive], use=True)
        assert seen["tag"] == "master-2"
        assert seen["variant"] == "vulkan"
        assert seen["archives"] == [archive]
        assert seen["use"] is True
        assert "画图引擎就绪" in capsys.readouterr().out

    def test_install_errors(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="不能同时使用"):
            llmpaint.engine_install_cmd(path=tmp_path, archives=[tmp_path / "a.zip"])
        with pytest.raises(ServeError, match="请用 --tag"):
            llmpaint.engine_install_cmd()

    def test_use_and_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        engine = SdEngine("master-3", tmp_path / "sd-server.exe")
        monkeypatch.setattr(llmpaint._llmpaint_engine, "use_sd_engine", lambda tag, home=None: engine)
        llmpaint.engine_use_cmd("master-3")
        assert "默认画图引擎" in capsys.readouterr().out

        monkeypatch.setattr(llmpaint._llmpaint_engine, "resolve_sd_engine", lambda tag=None, home=None: engine)
        monkeypatch.setattr(
            llmpaint._llmpaint_engine, "check_sd_engine", lambda item, runner=None: Check("画图引擎", "ok", "help ok")
        )
        llmpaint.engine_check_cmd()
        assert "help ok" in capsys.readouterr().out


class _StubServer:
    """只替换 llmpaint 实际用到的服务函数，其余委托原模块。"""

    def __init__(self, **overrides: Any) -> None:
        self._overrides = overrides
        self._real = _llmpaint_server

    def __getattr__(self, name: str) -> Any:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._real, name)


def _stub_server(**overrides: Any) -> _StubServer:
    return _StubServer(**overrides)


def test_scan_assets_sees_mainstream_image_families(tmp_path: Path) -> None:
    """qwen-image-2.1 / MiniMax-H3-Image 的通用文件名也要进入画图主模型候选。"""
    _weight(tmp_path / "qwen-image-2.1" / "model-Q4_K_M.gguf", 30)
    _weight(tmp_path / "MiniMax-H3-Image" / "model.safetensors", 20)
    assets = _llmpaint.scan_assets([tmp_path], min_bytes=2)
    names = {asset.path.parent.name for asset in assets.main_models()}
    assert names == {"qwen-image-2.1", "MiniMax-H3-Image"}
    assert {asset.family for asset in assets.main_models()} == {"qwen-image", "minimax-h3"}
