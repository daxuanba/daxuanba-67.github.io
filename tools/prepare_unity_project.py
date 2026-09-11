#!/usr/bin/env python3
"""
prepare_unity_project.py
------------------------
把 AssetRipper 导出的 Unity 工程，加工成「Unity 6000.0.35f1 可以直接打开并打 WebGL」的形态。

做三件事：
  1. 生成 Packages/manifest.json（版本从 packages.unity.com 实时解析，带内置兜底表）
  2. 隔离「本该由 Unity 包提供」的 DLL —— 它们留在 Assets/Plugins 会造成重复程序集、编译报错
  3. 隔离 WebGL 平台不可能加载的 Windows/Xbox 专属插件

所有被移走的文件都进 <project>/_quarantine/，不删除，随时可还原。

用法:
  python prepare_unity_project.py --project "D:/GetToWorkUnity/ExportedProject" \
      [--unity 6000.0] [--no-resolve] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

REGISTRY = "https://packages.unity.com/"

# ---------------------------------------------------------------- 包 → DLL 映射
# key: 包名, value: 该包提供的托管 DLL 文件名前缀/全名
PACKAGE_DLLS: dict[str, list[str]] = {
    "com.unity.addressables": [
        "Unity.Addressables.dll",
        "Unity.ResourceManager.dll",
        "Unity.ScriptableBuildPipeline.dll",
    ],
    "com.unity.render-pipelines.core": [
        "Unity.RenderPipelines.Core.Runtime.dll",
        "Unity.RenderPipelines.Core.Runtime.Shared.dll",
        "Unity.RenderPipelines.Core.ShaderLibrary.dll",
        "Unity.RenderPipeline.Universal.ShaderLibrary.dll",
    ],
    "com.unity.render-pipelines.universal": [
        "Unity.RenderPipelines.Universal.Runtime.dll",
        "Unity.RenderPipelines.Universal.Config.Runtime.dll",
        "Unity.RenderPipelines.Universal.2D.Runtime.dll",
        "Unity.RenderPipelines.Universal.Shaders.dll",
        "Unity.RenderPipelines.ShaderGraph.ShaderGraphLibrary.dll",
        "Unity.RenderPipelines.GPUDriven.Runtime.dll",
        "Unity.Rendering.LightTransport.Runtime.dll",
    ],
    "com.unity.burst": ["Unity.Burst.dll", "Unity.Burst.Unsafe.dll"],
    "com.unity.collections": [
        "Unity.Collections.dll",
        "Unity.Collections.LowLevel.ILSupport.dll",
    ],
    "com.unity.mathematics": ["Unity.Mathematics.dll"],
    "com.unity.ugui": ["UnityEngine.UI.dll", "Unity.TextMeshPro.dll"],
    "com.unity.timeline": ["Unity.Timeline.dll"],
    "com.unity.visualeffectgraph": ["Unity.VisualEffectGraph.Runtime.dll"],
    "com.unity.probuilder": [
        "Unity.ProBuilder.dll",
        "Unity.ProBuilder.Csg.dll",
        "Unity.ProBuilder.KdTree.dll",
        "Unity.ProBuilder.Poly2Tri.dll",
        "Unity.ProBuilder.Stl.dll",
    ],
    "com.unity.cinemachine": ["Cinemachine.dll"],
    "com.unity.ai.navigation": ["Unity.AI.Navigation.dll"],
    "com.unity.2d.tilemap.extras": ["Unity.2D.Tilemap.Extras.dll"],
    "com.unity.postprocessing": ["Unity.Postprocessing.Runtime.dll"],
    "com.unity.multiplayer.center": ["Unity.Multiplayer.Center.Common.dll"],
    "com.unity.services.core": [
        "Unity.Services.Core.dll",
        "Unity.Services.Core.Analytics.dll",
        "Unity.Services.Core.Components.dll",
        "Unity.Services.Core.Configuration.dll",
        "Unity.Services.Core.Device.dll",
        "Unity.Services.Core.Environments.dll",
        "Unity.Services.Core.Environments.Internal.dll",
        "Unity.Services.Core.Internal.dll",
        "Unity.Services.Core.Networking.dll",
        "Unity.Services.Core.Registration.dll",
        "Unity.Services.Core.Scheduler.dll",
        "Unity.Services.Core.Telemetry.dll",
        "Unity.Services.Core.Threading.dll",
    ],
    "com.unity.services.analytics": ["Unity.Services.Analytics.dll"],
    "com.unity.profiling.core": ["Unity.Profiling.Core.dll"],
    "com.unity.memoryprofiler": ["Unity.MemoryProfiler.dll"],
    "com.unity.recorder": ["Unity.Recorder.dll", "Unity.Recorder.Base.dll"],
}

# Unity 6000.0.35f1 自带包的权威版本。
# 依据：<Editor>/Data/Resources/PackageManager/{BuiltInPackages/*/package.json, Editor/*.tgz}
# 游戏就是用这个编辑器构建的，因此这套版本与反编译代码的 API 最匹配。
PINNED_VERSIONS: dict[str, str] = {
    "com.unity.render-pipelines.core": "17.0.3",
    "com.unity.render-pipelines.universal": "17.0.3",
    "com.unity.shadergraph": "17.0.3",
    "com.unity.visualeffectgraph": "17.0.3",
    "com.unity.ugui": "2.0.0",
    "com.unity.multiplayer.center": "1.0.0",
    "com.unity.addressables": "2.2.2",
    "com.unity.cinemachine": "2.10.3",
    "com.unity.timeline": "1.8.7",
    "com.unity.burst": "1.8.18",
    "com.unity.collections": "2.5.1",
    "com.unity.mathematics": "1.3.2",
    "com.unity.probuilder": "6.0.4",
    "com.unity.ai.navigation": "2.0.5",
    "com.unity.2d.tilemap.extras": "4.1.0",
    "com.unity.postprocessing": "3.4.0",
    "com.unity.services.core": "1.14.0",
    "com.unity.services.analytics": "6.0.1",
    "com.unity.profiling.core": "1.0.2",
    "com.unity.memoryprofiler": "1.1.1",
    "com.unity.recorder": "5.1.2",
    "com.unity.inputsystem": "1.11.2",
}

# 兼容旧名
FALLBACK_VERSIONS = PINNED_VERSIONS


def editor_package_versions(unity_dir: str) -> dict[str, str]:
    """直接从 Unity 安装目录读出该编辑器自带的包版本（最权威）。"""
    out: dict[str, str] = {}
    pm = Path(unity_dir) / "Editor" / "Data" / "Resources" / "PackageManager"
    builtin = pm / "BuiltInPackages"
    if builtin.is_dir():
        for d in builtin.iterdir():
            pj = d / "package.json"
            if pj.is_file():
                try:
                    info = json.loads(pj.read_text(encoding="utf-8"))
                    if info.get("name") and info.get("version"):
                        out[info["name"]] = info["version"]
                except Exception:  # noqa: BLE001
                    pass
    bundled = pm / "Editor"
    if bundled.is_dir():
        for f in bundled.iterdir():
            m = re.match(r"^(com\.unity\.[a-z0-9.\-]+)-(\d[^/]*?)\.tgz$", f.name)
            if m:
                out.setdefault(m.group(1), m.group(2))
    return out

# Unity 内置模块（必须有，否则引擎功能缺失）
BUILTIN_MODULES = [
    "com.unity.modules.ai",
    "com.unity.modules.androidjni",
    "com.unity.modules.animation",
    "com.unity.modules.assetbundle",
    "com.unity.modules.audio",
    "com.unity.modules.cloth",
    "com.unity.modules.director",
    "com.unity.modules.imageconversion",
    "com.unity.modules.imgui",
    "com.unity.modules.jsonserialize",
    "com.unity.modules.particlesystem",
    "com.unity.modules.physics",
    "com.unity.modules.physics2d",
    "com.unity.modules.screencapture",
    "com.unity.modules.terrain",
    "com.unity.modules.terrainphysics",
    "com.unity.modules.tilemap",
    "com.unity.modules.ui",
    "com.unity.modules.uielements",
    "com.unity.modules.umbra",
    "com.unity.modules.unityanalytics",
    "com.unity.modules.unitywebrequest",
    "com.unity.modules.unitywebrequestassetbundle",
    "com.unity.modules.unitywebrequestaudio",
    "com.unity.modules.unitywebrequesttexture",
    "com.unity.modules.unitywebrequestwww",
    "com.unity.modules.vehicles",
    "com.unity.modules.video",
    "com.unity.modules.vr",
    "com.unity.modules.wind",
    "com.unity.modules.xr",
]

# 真正无法在 WebGL 加载的**原生二进制**（不是托管 DLL）→ 移出工程。
# 注意：托管 DLL（Rewired_Windows / Unity.Microsoft.GDK / steamworks.net / _Isto.Core.Xbox）
# 一律**保留**——它们只是托管代码，能正常编译；只有在运行时才可能因缺原生库而报错。
# 贸然移除会导致反编译代码编译失败（实测：XBoxGameData.cs 依赖 Isto.Core.Platforms.Xbox）。
NATIVE_WINDOWS_ONLY = [
    "steam_api64.dll",
    "steam_api.dll",
    "fmodstudio.dll",
    "fmod.dll",
    "Rewired_DirectInput.dll",
    "Rewired_WindowsGamingInput.dll",
    "Rewired_StandaloneWindows.dll",
]

# 兼容旧名
WINDOWS_ONLY_DLLS = NATIVE_WINDOWS_ONLY


# ------------------------------------------------------------------ 版本解析
def _ver_key(v: str) -> tuple:
    """把 '17.0.3-preview.1' 变成可排序的元组。"""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-([\w.]+))?", v)
    if not m:
        return (0, 0, 0, 1, v)
    major, minor, patch = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    pre = m.group(4)
    return (major, minor, patch, 0 if pre else 1, pre or "")


def resolve_version(pkg: str, unity: str) -> str | None:
    """从 packages.unity.com 找出兼容 <unity> 的最高稳定版本。"""
    try:
        with urllib.request.urlopen(REGISTRY + pkg, timeout=25) as r:
            data = json.load(r)
    except Exception as exc:  # noqa: BLE001
        print(f"    ! 无法查询 {pkg}: {exc}")
        return None

    want = tuple(int(x) for x in unity.split(".")[:2])
    cands: list[str] = []
    for ver, info in data.get("versions", {}).items():
        if "-" in ver:  # 跳过 preview / experimental
            continue
        req = info.get("unity") or "0.0"
        try:
            req_t = tuple(int(x) for x in req.split(".")[:2])
        except ValueError:
            continue
        if req_t <= want:
            cands.append(ver)

    if not cands:
        return None
    return sorted(cands, key=_ver_key)[-1]


# ------------------------------------------------------------------ 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="AssetRipper 导出的 ExportedProject 目录")
    ap.add_argument("--unity", default="6000.0", help="目标 Unity 主版本，默认 6000.0")
    ap.add_argument("--unity-dir", default=None,
                    help="Unity 安装根目录（如 D:/Unity/6000.0.35f1），用于读取自带包版本")
    ap.add_argument("--no-resolve", action="store_true", help="跳过联网解析，只用钉死版本表")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不落盘")
    args = ap.parse_args()

    project = Path(args.project)
    if not (project / "Assets").is_dir():
        print(f"[X] 不是有效的 Unity 工程: {project}")
        return 1

    quarantine = project / "_quarantine"
    plugins = project / "Assets" / "Plugins"

    # ---------------------------------------------------------- 1. manifest.json
    print("[1/3] 生成 Packages/manifest.json")
    from_editor: dict[str, str] = {}
    if args.unity_dir and Path(args.unity_dir).is_dir():
        from_editor = editor_package_versions(args.unity_dir)
        print(f"    从编辑器读取到 {len(from_editor)} 个自带包版本")

    deps: dict[str, str] = {}
    wanted = list(PACKAGE_DLLS) + ["com.unity.shadergraph", "com.unity.inputsystem"]
    for pkg in wanted:
        ver, src = None, None
        if pkg in from_editor:                       # 1) 编辑器自带（最权威）
            ver, src = from_editor[pkg], "editor"
        elif pkg in PINNED_VERSIONS:                 # 2) 钉死表
            ver, src = PINNED_VERSIONS[pkg], "pinned"
        elif not args.no_resolve:                    # 3) 联网解析
            r = resolve_version(pkg, args.unity)
            if r:
                ver, src = r, "registry"
        if not ver:
            print(f"    - 跳过 {pkg}（无可用版本）")
            continue
        deps[pkg] = ver
        print(f"    + {pkg} = {ver}  ({src})")

    for m in BUILTIN_MODULES:
        deps[m] = "1.0.0"

    manifest = {"dependencies": dict(sorted(deps.items()))}
    manifest_dir = project / "Packages"
    if args.dry_run:
        print(f"    (dry-run) 将写入 {manifest_dir / 'manifest.json'}")
    else:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"    -> {manifest_dir / 'manifest.json'} ({len(deps)} 个依赖)")

    # ------------------------------------------------- 2. 隔离包提供的重复 DLL
    print("[2/3] 隔离 Unity 包提供的托管 DLL（避免重复程序集）")
    pkg_dll_names = {n.lower() for names in PACKAGE_DLLS.values() for n in names}
    moved_pkg: list[str] = []
    if plugins.is_dir():
        for f in sorted(plugins.iterdir()):
            if f.is_file() and f.name.lower() in pkg_dll_names:
                moved_pkg.append(f.name)
                if not args.dry_run:
                    dest_dir = quarantine / "package_dlls"
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    for suffix in ("", ".meta"):
                        src = plugins / (f.name + suffix)
                        if src.exists():
                            shutil.move(str(src), str(dest_dir / (f.name + suffix)))
    print(f"    移出 {len(moved_pkg)} 个: {', '.join(moved_pkg) if moved_pkg else '无'}")

    # --------------------------------------------- 3. 隔离 Windows/Xbox 专属插件
    print("[3/3] 隔离 WebGL 无法加载的 Windows/Xbox 插件")
    moved_win: list[str] = []
    if plugins.is_dir():
        for f in sorted(plugins.iterdir()):
            if f.is_file() and f.name.lower() in {n.lower() for n in WINDOWS_ONLY_DLLS}:
                moved_win.append(f.name)
                if not args.dry_run:
                    dest_dir = quarantine / "windows_only"
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    for suffix in ("", ".meta"):
                        src = plugins / (f.name + suffix)
                        if src.exists():
                            shutil.move(str(src), str(dest_dir / (f.name + suffix)))
    print(f"    移出 {len(moved_win)} 个: {', '.join(moved_win) if moved_win else '无'}")

    print("\n完成。" + ("（dry-run，未改动磁盘）" if args.dry_run else ""))
    if not args.dry_run:
        print(f"隔离区: {quarantine}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
