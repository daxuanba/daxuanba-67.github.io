#!/usr/bin/env python3
"""Split a Unity WebGL build into GitHub-friendly parts and emit build.json.

GitHub rejects any single file larger than 100 MB and Pages caps a published
site at 1 GB, so a Unity WebGL build has to be chopped into pieces.  This script:

  1. copies the Unity build output into  <site>/game/build/
  2. splits every file larger than --part-size into  <file>.part000  chunks
  3. writes  <site>/game/build.json  describing the whole layout

game/unity-loader.js then downloads every chunk, stitches the pieces back
together in memory and hands them to Unity, so the game boots directly from
the static site with no server-side logic.

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

PART_FMT = ".part{:03d}"

# Unity emits these four artefacts; map filename suffix -> config key.
# Longer suffixes must be tested first (.data.br before .data).
SUFFIX_MAP = [
    (".framework.js.br", "frameworkUrl"),
    (".framework.js.gz", "frameworkUrl"),
    (".framework.js", "frameworkUrl"),
    (".loader.js", "loaderUrl"),
    (".wasm.br", "codeUrl"),
    (".wasm.gz", "codeUrl"),
    (".wasm", "codeUrl"),
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


def human(n: int) -> str:
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


def split_file(src: Path, out_dir: Path, manifest_rel: str, part_size: int) -> dict:
    """Copy <src> into out_dir (or split it) and return its manifest entry.

    out_dir       -> <site>/game/build        (where bytes physically land)
    manifest_rel  -> "build/<name>"           (path the browser will request)
    """
    name = manifest_rel.split("/")[-1]
    size = src.stat().st_size
    dest = out_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "path": manifest_rel,
        "size": size,
        "sha256": sha256_of(src),
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
            part_name = name + PART_FMT.format(idx)
            (out_dir / part_name).write_bytes(chunk)
            parts.append({"url": "build/" + part_name, "size": len(chunk)})
            idx += 1
    entry["parts"] = parts
    return entry


def main() -> int:
    ap = argparse.ArgumentParser(description="Unity WebGL build -> GitHub-friendly parts")
    ap.add_argument("--build", required=True, help="Unity WebGL output dir (contains Build/)")
    ap.add_argument("--site", default=".", help="repo root that holds game/ (default: .)")
    ap.add_argument("--part-size", type=int, default=95,
                    help="max size of one part in MB (default 95, GitHub limit is 100)")
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

    # Unity config keys are relative to game/build.json -> prefix with build/
    config = {
        "companyName": args.company,
        "productName": args.product,
        "productVersion": args.version,
        "streamingAssetsUrl": None,
    }

    entries = []
    copied_any = False
    for key, path in unity.items():
        rel = "build/" + path.name
        entry = split_file(path, out_dir, rel, part_size)
        entries.append(entry)
        config[key] = rel
        copied_any = True
        n = len(entry["parts"] or [])
        flag = f"切成 {n} 片" if n > 1 else "单文件"
        print(f"[+] {rel}  -> {flag}")

    # StreamingAssets (loose files) - kept as-is, must stay under the limits.
    sa_src = None
    for cand in (build_dir / "StreamingAssets", build_dir.parent / "StreamingAssets",
                 build_dir / "Build" / "StreamingAssets"):
        if cand.is_dir():
            sa_src = cand
            break
    if sa_src:
        dst = out_dir / "StreamingAssets"
        shutil.copytree(sa_src, dst, dirs_exist_ok=True)
        config["streamingAssetsUrl"] = "build/StreamingAssets"
        print(f"[+] StreamingAssets -> {sum(1 for _ in dst.rglob('*') if _.is_file())} 个文件")
        oversize = [p for p in dst.rglob("*") if p.is_file() and p.stat().st_size > part_size]
        if oversize:
            print(f"[!] 警告: StreamingAssets 里有 {len(oversize)} 个文件超过分片阈值，"
                  f"Pages 上传会失败（{oversize[0].name} …）")

    if not copied_any:
        raise SystemExit("[x] 没有可复制的 Unity 产物")

    manifest = {
        "mode": "webgl",
        "generated_by": "tools/split_package.py",
        "partSize": part_size,
        "unity": config,
        "files": entries,
        "totalBytes": sum(e["size"] for e in entries),
    }
    manifest_path = game_dir / "build.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    total_parts = sum(len(e["parts"] or []) for e in entries)
    print(f"[=] build.json 已生成: {manifest_path}")
    print(f"[=] 总大小 {human(manifest['totalBytes'])}，共 {total_parts} 个文件（含分片）")
    if manifest["totalBytes"] > 1024 ** 3:
        print("[!] 注意: 构建体积超过 GitHub Pages 的 1 GB 站点软上限，"
              "Pages 可能拒绝发布；建议改用本地服务或对象存储。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
