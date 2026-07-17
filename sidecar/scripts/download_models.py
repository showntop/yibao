"""下载 Plan 4a 语音模型：Paraformer 中文 int8 + Silero VAD。

用法：uv run --directory sidecar python scripts/download_models.py
模型落到 src/yibao_brain/models/（.gitignore 忽略大文件）。
"""
import os
import shutil
import tarfile
import urllib.request

MODELS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src", "yibao_brain", "models")
)

PARAFORMER_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-paraformer-zh-2023-09-14.tar.bz2"
)
PARAFORMER_EXTRACTED = "sherpa-onnx-paraformer-zh-2023-09-14"
VAD_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"


def _download(url: str, dst: str) -> None:
    print(f"下载 {url}\n  → {dst}")
    urllib.request.urlretrieve(url, dst)


def main() -> int:
    os.makedirs(MODELS_DIR, exist_ok=True)

    vad_dst = os.path.join(MODELS_DIR, "silero_vad.onnx")
    if not os.path.exists(vad_dst):
        _download(VAD_URL, vad_dst)
    else:
        print(f"已存在：{vad_dst}")

    para_dir = os.path.join(MODELS_DIR, "paraformer-zh")
    if not os.path.exists(os.path.join(para_dir, "model.int8.onnx")):
        tbz = os.path.join(MODELS_DIR, "paraformer.tar.bz2")
        _download(PARAFORMER_URL, tbz)
        print("解压...")
        with tarfile.open(tbz, "r:bz2") as t:
            t.extractall(MODELS_DIR)
        extracted = os.path.join(MODELS_DIR, PARAFORMER_EXTRACTED)
        if os.path.isdir(extracted):
            if os.path.isdir(para_dir):
                shutil.rmtree(para_dir)
            os.rename(extracted, para_dir)
        os.remove(tbz)
    else:
        print(f"已存在：{para_dir}")

    print(f"\n模型就绪：{MODELS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
