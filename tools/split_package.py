#!/usr/bin/env python3
"""Split a Unity WebGL build into GitHub-friendly parts and emit build.json.

GitHub rejects any single file larger than 100 MB and Pages caps a published
site at 1 GB, so a Unity WebGL build has to be chopped into pieces.  This script:

  1. copies the Unity build output into  <site>/game/build/
  2. splits every file larger than --part-size into  <file>.part000  chunks
     (this includes StreamingAssets — Addressables bundles are routinely
     100-220 MB each, and MUST be split or `git push` is rejected)
  3. writes  <site>/game/build.json  describing the whole layout

game/unity-loader.js then downloads every chunk, stitches the pieces back
together in memory and hands them to Unity — including the StreamingAssets
bundles, which it serves through a fetch/XHR interceptor so the engine never
sees the split files.

Usage:
    python tools/split_package.py --build "D:/Build_WebGL" --site .
    python tools/split_package.py --build <dir> --site <repo-root> --part-size 90
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

# Shards keep a ".bin" tail on purpose: some CDNs (jsDelivr) key their
# content-type / serving rules off the extension, and a bare ".part000" is
# treated as an unknown extension.  ".bin" always serves as octet-stream.
PART_FMT = ".part{:03d}.bin"

# Unity emits these four artefacts; map filename suffix -> config key.
# Longer suffixes must be tested first (.data.br before .data).
SUFFIX_MAP = [
    (".framework.js.unityweb", "frameworkUrl"),
    (".framework.js.br", "frameworkUrl"),
    (".framework.js.gz", "frameworkUrl"),
    (".framework.js", "frameworkUrl"),
    (".loader.js", "loaderUrl"),
    (".wasm.unityweb", "codeUrl"),
    (".wasm.br", "codeUrl"),
    (".wasm.gz", "codeUrl"),
    (".wasm", "codeUrl"),
    (".data.unityweb", "dataUrl"),
    (".data.br", "dataUrl"),
    (".data.gz", "dataUrl"),
    (".data", "dataUrl"),
]

MIME = {
    "loaderUrl": "text/javascript",
    "frameworkUrl": "text/javascript",
    "dataUrl": "application/octet-stream",
    "codeUrl": "application/wasm",
}

# Addressables bundles / FMOD banks — anything the engine pulls itself.
STREAMING_ASSET_EXT = {
    ".bundle": "application/octet-stream",
    ".bank": "application/octet-stream",
    ".bin": "application/octet-stream",
    ".json": "application/json",
    ".hash": "application/octet-stream",
}


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} GB"


def sha256_of(path: Path, buf_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(buf_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _dest_for(out_dir: Path, manifest_rel: str) -> Path:
    """manifest_rel -> physical path under out_dir (drops a leading 'build/')."""
    rel = manifest_rel[6:] if manifest_rel.startswith("build/") else manifest_rel
    return out_dir / rel


def detect_unity(build_dir: Path) -> dict:
    """Find the four Unity artefacts and return {configKey: relative posix path}."""
    found: dict[str, Path] = {}
    for entry in sorted(build_dir.rglob("*")):
        if not entry.is_file():
            continue
        name = entry.name
        for suffix, key in SUFFIX_MAP:
            if name.endswith(suffix) and key not in found:
                found[key] = entry
                break
    missing = [k for k in ("loaderUrl", "dataUrl", "frameworkUrl", "codeUrl") if k not in found]
    if missing:
        raise SystemExit(
            f"[x] 在 {build_dir} 里找不到这些 Unity 产物: {', '.join(missing)}\n"
            "    请确认 --build 指向 Unity WebGL 构建的输出目录（含 .loader.js/.data/.framework.js/.wasm）。"
        )
    return {k: v for k, v in found.items()}


def split_file(src: Path, out_dir: Path, manifest_rel: str, part_size: int,
               mime: str | None = None) -> dict:
    """Copy <src> into out_dir (or split it) and return its manifest entry.

    manifest_rel -> "build/<sub/path>/<name>"  (path the browser requests)
    """
    size = src.stat().st_size
    dest = _dest_for(out_dir, manifest_rel)
    dest.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "path": manifest_rel,
        "size": size,
        "sha256": sha256_of(src),
        "type": mime or "application/octet-stream",
        "parts": None,
    }

    if size <= part_size:
        shutil.copy2(src, dest)
        entry["parts"] = [{"url": manifest_rel, "size": size}]
        return entry

    # Split into chunks.  `parts` lists the URLs the browser must fetch in order.
    parts = []
    with src.open("rb") as fh:
        idx = 0
        while True:
            chunk = fh.read(part_size)
            if not chunk:
                break
            part_name = dest.name + PART_FMT.format(idx)
            (dest.parent / part_name).write_bytes(chunk)
            parts.append({
                "url": manifest_rel + PART_FMT.format(idx),
                "size": len(chunk),
            })
            idx += 1
    entry["parts"] = parts
    return entry


def main() -> int:
    ap = argparse.ArgumentParser(description="Unity WebGL build -> GitHub-friendly parts")
    ap.add_argument("--build", required=True, help="Unity WebGL output dir (contains Build/)")
    ap.add_argument("--site", default=".", help="repo root that holds game/ (default: .)")
    ap.add_argument("--part-size", type=int, default=95,
                    help="max size of one part in MB (default 95, GitHub limit is 100)")
    ap.add_argument("--data-base", default="",
                    help="prefix for part URLs, e.g. "
                         "https://raw.githubusercontent.com/<user>/<repo>/master/ "
                         "(empty => serve parts from the same origin)")
    ap.add_argument("--company", default="Isto")
    ap.add_argument("--product", default="Get To Work")
    ap.add_argument("--version", default="1.0")
    args = ap.parse_args()

    build_dir = Path(args.build).expanduser().resolve()
    site = Path(args.site).expanduser().resolve()
    if not build_dir.is_dir():
        raise SystemExit(f"[x] 构建目录不存在: {build_dir}")

    part_size = args.part_size * 1024 * 1024
    game_dir = site / "game"
    out_dir = game_dir / "build"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    unity = detect_unity(build_dir)
    print("[*] 识别到 Unity 产物:")
    for key, path in unity.items():
        print(f"    {key:14} {path.name}  ({human(path.stat().st_size)})")

    config = {
        "companyName": args.company,
        "productName": args.product,
        "productVersion": args.version,
        "streamingAssetsUrl": None,
    }

    entries = []
    for key, path in unity.items():
        rel = "build/" + path.name
        entry = split_file(path, out_dir, rel, part_size, MIME.get(key))
        entries.append(entry)
        config[key] = rel
        n = len(entry["parts"])
        print(f"[+] {rel}  -> {'切成 %d 片' % n if n > 1 else '单文件'}")

    # ---- StreamingAssets -----------------------------------------------------
    # Addressables bundles land here (build/StreamingAssets/aa/WebGL/*.bundle) and
    # are far over GitHub's 100 MB file limit, so they get split like everything
    # else.  unity-loader.js intercepts the engine's requests for these paths and
    # serves the stitched Blob instead.
    sa_src = None
    for cand in (build_dir / "StreamingAssets", build_dir.parent / "StreamingAssets",
                 build_dir / "Build" / "StreamingAssets"):
        if cand.is_dir():
            sa_src = cand
            break

    sa_files = []
    if sa_src:
        all_files = sorted(p for p in sa_src.rglob("*") if p.is_file())
        for p in all_files:
            rel_path = p.relative_to(sa_src).as_posix()
            manifest_rel = "build/StreamingAssets/" + rel_path
            mime = STREAMING_ASSET_EXT.get(p.suffix.lower(), "application/octet-stream")
            entry = split_file(p, out_dir, manifest_rel, part_size, mime)
            entries.append(entry)
            sa_files.append(entry)
        config["streamingAssetsUrl"] = "build/StreamingAssets"
        big = sum(1 for e in sa_files if len(e["parts"]) > 1)
        print(f"[+] StreamingAssets -> {len(sa_files)} 个文件（其中 {big} 个被分片）")

    if not entries:
        raise SystemExit("[x] 没有可复制的 Unity 产物")

    manifest = {
        "mode": "webgl",
        "generated_by": "tools/split_package.py",
        "partSize": part_size,
        "dataBase": args.data_base or "",
        "unity": config,
        "files": entries,
        "totalBytes": sum(e["size"] for e in entries),
    }
    manifest_path = game_dir / "build.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    total_parts = sum(len(e["parts"] or []) for e in entries)
    print(f"[=] build.json 已生成: {manifest_path}")
    print(f"[=] 原始内容 {human(manifest['totalBytes'])}，实际落盘 {total_parts} 个文件（含分片）")
    if manifest["totalBytes"] > 1024 ** 3:
        print("[!] 注意: 构建体积超过 GitHub Pages 的 1 GB 站点软上限，"
              "Pages 可能拒绝发布；建议改用对象存储或 GitHub Releases。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
