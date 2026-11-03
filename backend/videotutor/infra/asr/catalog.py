"""What can be downloaded for speech recognition, pinned to exact bytes.

Models come from Hugging Face at a fixed commit (hf-mirror.com serves the
same paths inside China); the NVIDIA runtime comes from NVIDIA's own PyPI
wheels (Aliyun mirrors the same paths). Every file is pinned by sha256, so a
mirror can only ever deliver the original file or fail.

To change a model: pick the commit, regenerate the entries (size + sha256 of
every file at that commit), and give it a new component id -- an installed
copy is identified by id, so reusing one would keep the old files.
"""

from __future__ import annotations

from videotutor.domain.speech import Component, ComponentKind, RemoteFile

_HF = ("https://huggingface.co", "https://hf-mirror.com")
_PYPI = ("https://files.pythonhosted.org/packages", "https://mirrors.aliyun.com/pypi/packages")

GPU_RUNTIME_ID = "nvidia-cuda12"


def _hf_files(repo: str, rev: str, files: list[tuple[str, int, str]]) -> tuple[RemoteFile, ...]:
    return tuple(
        RemoteFile(name, size, sha, tuple(f"{host}/{repo}/resolve/{rev}/{name}" for host in _HF))
        for name, size, sha in files
    )


def _wheel(path: str, size: int, sha: str, extract: tuple[str, ...]) -> RemoteFile:
    name = path.rsplit("/", 1)[1]
    return RemoteFile(name, size, sha, tuple(f"{host}/{path}" for host in _PYPI), extract)


# Best first: choose_model() relies on this order.
SPEECH_MODELS: tuple[Component, ...] = (
    Component(
        id="whisper-large-v3-turbo",
        kind=ComponentKind.SPEECH_MODEL,
        title="高精度模型（large-v3-turbo）",
        description="最准确，适合 NVIDIA 显卡；只用 CPU 会很慢。",
        prefers_gpu=True,
        files=_hf_files(
            "deepdml/faster-whisper-large-v3-turbo-ct2",
            "4df90f75321148c3a29a9e2351b7ddf8f5b115a8",
            [
                (
                    "config.json",
                    2263,
                    "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
                ),
                (
                    "model.bin",
                    1617884929,
                    "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
                ),
                (
                    "preprocessor_config.json",
                    340,
                    "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
                ),
                (
                    "tokenizer.json",
                    2710337,
                    "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
                ),
                (
                    "vocabulary.json",
                    1068114,
                    "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
                ),
            ],
        ),
    ),
    Component(
        id="whisper-small.en",
        kind=ComponentKind.SPEECH_MODEL,
        title="标准模型（small.en）",
        description="准确度好，只用 CPU 也够快：10 分钟的视频大约 2 分钟识别完。推荐。",
        files=_hf_files(
            "Systran/faster-whisper-small.en",
            "d1d751a5f8271d482d14ca55d9e2deeebbae577f",
            [
                (
                    "config.json",
                    2657,
                    "666a9605530ac1f61fa8177f3702b4dacec9966749e42610839fcc32661d5fae",
                ),
                (
                    "model.bin",
                    483545366,
                    "62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a",
                ),
                (
                    "tokenizer.json",
                    2128466,
                    "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
                ),
                (
                    "vocabulary.txt",
                    422309,
                    "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf",
                ),
            ],
        ),
    ),
    Component(
        id="whisper-base.en",
        kind=ComponentKind.SPEECH_MODEL,
        title="轻量模型（base.en）",
        description="下载小、速度快，适合配置较低的电脑；口音重或有背景音乐时错误较多。",
        files=_hf_files(
            "Systran/faster-whisper-base.en",
            "3d3d5dee26484f91867d81cb899cfcf72b96be6c",
            [
                (
                    "config.json",
                    2227,
                    "f3bc3821e9fc76a27bae538e11ae5b677dcdd352b4600429ce7951d398569aeb",
                ),
                (
                    "model.bin",
                    145216508,
                    "2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef",
                ),
                (
                    "tokenizer.json",
                    2128466,
                    "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
                ),
                (
                    "vocabulary.txt",
                    422309,
                    "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf",
                ),
            ],
        ),
    ),
)

# cuBLAS + cuDNN (+ NVRTC, which some cuDNN engines compile with) for CUDA 12,
# the versions CTranslate2 4.x is built against. Only the DLLs are kept.
GPU_RUNTIME = Component(
    id=GPU_RUNTIME_ID,
    kind=ComponentKind.GPU_RUNTIME,
    title="NVIDIA 显卡加速",
    description="让语音识别用 NVIDIA 显卡运行，通常比 CPU 快 5–10 倍。需要较新的显卡驱动。",
    files=(
        _wheel(
            "45/a1/a17fade6567c57452cfc8f967a40d1035bb9301db52f27808167fbb2be2f/"
            "nvidia_cublas_cu12-12.9.1.4-py3-none-win_amd64.whl",
            553153899,
            "1e5fee10662e6e52bd71dec533fbbd4971bb70a5f24f3bc3793e5c2e9dc640bf",
            ("nvidia/cublas/bin/*.dll",),
        ),
        _wheel(
            "39/6a/5e9910b2b2c9dcddee9aaef372b3db0f08b9f7eaf1d462f859461a79caf9/"
            "nvidia_cudnn_cu12-9.8.0.87-py3-none-win_amd64.whl",
            684630375,
            "b4b5cfddc32aa4180f9d390ee99e9a9f55a89e7087329b41aba4319327e22466",
            ("nvidia/cudnn/bin/*.dll",),
        ),
        _wheel(
            "52/de/823919be3b9d0ccbf1f784035423c5f18f4267fb0123558d58b813c6ec86/"
            "nvidia_cuda_nvrtc_cu12-12.9.86-py3-none-win_amd64.whl",
            76408187,
            "72972ebdcf504d69462d3bcd67e7b81edd25d0fb85a2c46d3ea3517666636349",
            (
                "nvidia/cuda_nvrtc/bin/nvrtc64_120_0.dll",
                "nvidia/cuda_nvrtc/bin/nvrtc-builtins*.dll",
            ),
        ),
    ),
)

ALL: tuple[Component, ...] = (*SPEECH_MODELS, GPU_RUNTIME)
