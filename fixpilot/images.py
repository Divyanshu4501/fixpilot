"""
fixpilot/images.py

Docker image management for runs: SWE-bench images are 1-4 GB each, so a run pulls
them one batch at a time and removes them after scoring.

Safety rule: we only ever remove images that *this run pulled*. Images that were
already on disk (other projects, or ones you pulled by hand) are never touched.

Estimate download size without pulling anything:
    uv run python -m fixpilot.images data/dev_ids.txt
"""

import json
import shutil
import sys
import urllib.request

import docker
from docker.errors import APIError, ImageNotFound

REGISTRY = "https://registry-1.docker.io"
MANIFEST_TYPES = ", ".join([
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
])


def image_for(instance_id: str) -> str:
    """Docker Hub image name for a SWE-bench instance (`__` becomes `_1776_`)."""
    return f"swebench/sweb.eval.x86_64.{instance_id.replace('__', '_1776_').lower()}:latest"


def free_gb(path: str = "/") -> float:
    """Free disk space in GB."""
    return shutil.disk_usage(path).free / 1e9


def is_local(client: docker.DockerClient, image: str) -> bool:
    try:
        client.images.get(image)
        return True
    except ImageNotFound:
        return False


def ensure_image(client: docker.DockerClient, image: str) -> bool:
    """Make sure an image is on disk. Returns True if we pulled it (so we may remove it later)."""
    if is_local(client, image):
        return False
    repo, tag = image.rsplit(":", 1)
    client.images.pull(repo, tag=tag)
    return True


def remove_image(client: docker.DockerClient, image: str) -> bool:
    """Remove one image (never a prune). Returns True if it was removed."""
    try:
        client.images.remove(image)
        return True
    except ImageNotFound:
        return False
    except APIError as e:  # e.g. still used by a container: leave it, report it
        print(f"could not remove {image}: {e.explanation}", flush=True)
        return False


# ---------- size estimate from the registry (no pull) ----------

def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def layer_sizes(image: str) -> dict[str, int]:
    """Compressed layer sizes {digest: bytes} for a public Docker Hub image (linux/amd64)."""
    repo, tag = image.rsplit(":", 1)
    token = _get_json(f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull",
                      {})["token"]
    headers = {"Authorization": f"Bearer {token}", "Accept": MANIFEST_TYPES}
    manifest = _get_json(f"{REGISTRY}/v2/{repo}/manifests/{tag}", headers)
    if "manifests" in manifest:  # a multi-platform list: pick linux/amd64
        digest = next(m["digest"] for m in manifest["manifests"]
                      if m.get("platform", {}).get("architecture") == "amd64")
        manifest = _get_json(f"{REGISTRY}/v2/{repo}/manifests/{digest}", headers)
    return {layer["digest"]: layer["size"] for layer in manifest["layers"]}


def estimate_download_gb(images: list[str]) -> tuple[float, float]:
    """(sum of image sizes, size after de-duplicating shared layers), compressed, in GB."""
    unique: dict[str, int] = {}
    total = 0
    for image in images:
        sizes = layer_sizes(image)
        total += sum(sizes.values())
        unique.update(sizes)
    return total / 1e9, sum(unique.values()) / 1e9


if __name__ == "__main__":
    from fixpilot.tasks import read_ids

    ids = read_ids(sys.argv[1])
    images = [image_for(i) for i in ids]
    client = docker.from_env()
    local = [im for im in images if is_local(client, im)]
    total, unique = estimate_download_gb([im for im in images if im not in local])
    print(f"{len(ids)} tasks, {len(local)} images already on disk")
    print(f"to download (compressed): {total:.1f} GB summed, {unique:.1f} GB after shared layers")
    print(f"free disk: {free_gb():.0f} GB")
