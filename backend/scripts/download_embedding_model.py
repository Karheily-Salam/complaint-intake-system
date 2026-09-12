"""Fetch the multilingual embedding model used by EMBEDDING_PROVIDER=onnx.

    cd backend
    pip install -e ".[ml-onnx]"
    python scripts/download_embedding_model.py [--dir models/multilingual-e5-small]

Downloads the int8-quantised ONNX export of intfloat/multilingual-e5-small
(MIT licence) and its tokenizer from Hugging Face, pinned to one upstream
revision and checked against pinned SHA-256 digests - a changed or
corrupted file is refused rather than loaded. Nothing is uploaded and no
account or API key is involved. The files (~120 MB) are git-ignored.

In production, run it inside the backend container with the model directory
on a volume, then set EMBEDDING_PROVIDER=onnx and raise the container's
memory limit (see docs/ml.md).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
# remote path -> local file name
FILES = {
    "onnx/model_qint8_avx512_vnni.onnx": "model_qint8_avx512_vnni.onnx",
    "onnx/tokenizer.json": "tokenizer.json",
}
PINNED_SHA256 = {
    "model_qint8_avx512_vnni.onnx": (
        "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88"
    ),
    "tokenizer.json": "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(remote: str, target: Path) -> None:
    url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{remote}"
    partial = target.with_suffix(target.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        with partial.open("wb") as fh:
            for chunk in response.iter_bytes(1 << 20):
                fh.write(chunk)
    partial.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dir", default=str(BACKEND_DIR / "models" / "multilingual-e5-small"),
        help="where to put the model files",
    )
    args = parser.parse_args()
    target_dir = Path(args.dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    for remote, local in FILES.items():
        path = target_dir / local
        expected = PINNED_SHA256[local]
        if path.exists() and sha256(path) == expected:
            print(f"ok        {local} (already present)")
            continue
        print(f"download  {remote}")
        download(remote, path)
        actual = sha256(path)
        if actual != expected:
            path.unlink()
            print(f"REFUSED   {local}: sha256 {actual} != pinned {expected}", file=sys.stderr)
            return 1
        print(f"ok        {local} sha256={actual}")
    print(f"\nModel ready in {target_dir}. Set EMBEDDING_PROVIDER=onnx to use it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
