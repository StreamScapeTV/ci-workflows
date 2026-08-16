from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from urllib.request import Request, urlopen

IMAGE_ROOT = Path(__file__).resolve().parent
TOOLCHAIN = IMAGE_ROOT / "toolchain.lock.json"
DESTINATION = IMAGE_ROOT / ".ciw-build-inputs"


def _inputs(lock: dict[str, object]) -> tuple[tuple[str, str, str, int], ...]:
    flux = lock["flux"]
    kubectl = lock["kubectl"]
    yq = lock["yq"]
    helm = lock["helm"]
    kustomize = lock["kustomize"]
    assert isinstance(flux, dict)
    assert isinstance(kubectl, dict)
    assert isinstance(yq, dict)
    assert isinstance(helm, dict)
    assert isinstance(kustomize, dict)

    flux_version = str(flux["version"])
    kubectl_version = str(kubectl["version"])
    yq_version = str(yq["version"])
    helm_version = str(helm["version"])
    kustomize_version = str(kustomize["version"])
    return (
        (
            f"https://github.com/fluxcd/flux2/releases/download/v{flux_version}/flux_{flux_version}_linux_amd64.tar.gz",
            str(flux["linux_amd64_sha256"]),
            f"flux-{flux_version}-linux-amd64.tar.gz",
            64 * 1024 * 1024,
        ),
        (
            f"https://dl.k8s.io/release/v{kubectl_version}/bin/linux/amd64/kubectl",
            str(kubectl["linux_amd64_sha256"]),
            f"kubectl-{kubectl_version}-linux-amd64",
            64 * 1024 * 1024,
        ),
        (
            f"https://github.com/mikefarah/yq/releases/download/v{yq_version}/yq_linux_amd64",
            str(yq["linux_amd64_sha256"]),
            f"yq-{yq_version}-linux-amd64",
            20 * 1024 * 1024,
        ),
        (
            f"https://get.helm.sh/helm-v{helm_version}-linux-amd64.tar.gz",
            str(helm["linux_amd64_sha256"]),
            f"helm-{helm_version}-linux-amd64.tar.gz",
            64 * 1024 * 1024,
        ),
        (
            f"https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/v{kustomize_version}/kustomize_v{kustomize_version}_linux_amd64.tar.gz",
            str(kustomize["linux_amd64_sha256"]),
            f"kustomize-{kustomize_version}-linux-amd64.tar.gz",
            16 * 1024 * 1024,
        ),
    )


def _download(url: str, expected_sha256: str, name: str, maximum_bytes: int) -> None:
    if len(expected_sha256) != 64:
        raise RuntimeError(f"invalid checksum for {name}")
    int(expected_sha256, 16)
    request = Request(url, headers={"User-Agent": "ci-workflows-runner-image-build"})
    with urlopen(request, timeout=90) as response:
        data = response.read(maximum_bytes + 1)
    if len(data) > maximum_bytes:
        raise RuntimeError(f"input too large: {name}")
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise RuntimeError(f"input digest mismatch: {name}")
    (DESTINATION / name).write_bytes(data)


def main() -> None:
    lock = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1 or lock.get("product_id") != "runner-flux-control":
        raise RuntimeError("invalid Flux-control toolchain lock")
    DESTINATION.mkdir(exist_ok=False)
    try:
        for item in _inputs(lock):
            _download(*item)
    except BaseException:
        shutil.rmtree(DESTINATION, ignore_errors=True)
        raise


if __name__ == "__main__":
    main()
