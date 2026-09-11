#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Get To Work · WebGL 一键构建脚本
把 Steam PC 版（Unity 6000.0.35f1 / IL2CPP）重新编译成网页可玩版，
产物与 build.json 直接落到 game/build/，站点进入即自动加载。

用法：
  python tools/build_webgl.py \
      --unity "C:/Program Files/Unity 6000.0.35f1/Editor/Unity.exe" \
      --project "D:/gtw_export/Project" \
      --out "game/build"

前置：
  - Unity 6000.0.35f1 Editor（含 WebGL Build Support + Windows IL2CPP）
  - AssetRipper 2.x 已把游戏反编译成 Unity 工程（--project 指向该工程根）
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

# 注入到工程里的 Editor 构建脚本（自动找场景、打 WebGL）
EDITOR_CS = r'''
using UnityEditor;
using UnityEngine;
using System.IO;
using System.Linq;
using System.Collections.Generic;

public class BuildWebGL
{
    public static void PerformBuild()
    {
        string outDir = "Builds/WebGL";
        if (Directory.Exists(outDir)) Directory.Delete(outDir, true);
        Directory.CreateDirectory(outDir);

        var scenes = new List<string>();
        foreach (var s in EditorBuildSettings.scenes)
            if (s.enabled) scenes.Add(s.path);
        if (scenes.Count == 0)
        {
            foreach (var g in AssetDatabase.FindAssets("t:Scene"))
                scenes.Add(AssetDatabase.GUIDToAssetPath(g));
        }

        var opts = new BuildPlayerOptions();
        opts.scenes = scenes.ToArray();
        opts.locationPathName = outDir;
        opts.target = BuildTarget.WebGL;
        opts.options = BuildOptions.None;

        var report = BuildPipeline.BuildPlayer(opts);
        if (report.summary.result != UnityEditor.Build.Reporting.BuildResult.Success)
        {
            Debug.LogError("WebGL build failed: " + report.summary.result);
            EditorApplication.Exit(1);
        }
        EditorApplication.Exit(0);
    }
}
'''

CONTEXT_ATTRS = {
    "powerPreference": "high-performance",
    "preserveDrawingBuffer": False,
    "failIfMajorPerformanceCaveat": False,
    "antialias": True,
    "alpha": False,
}


def run_unity(unity_exe, project, log_path):
    editor_dir = os.path.join(project, "Assets", "Editor")
    os.makedirs(editor_dir, exist_ok=True)
    cs_path = os.path.join(editor_dir, "BuildWebGL.cs")
    with open(cs_path, "w", encoding="utf-8") as f:
        f.write(EDITOR_CS)

    cmd = [
        unity_exe, "-batchmode", "-quit", "-nographics",
        "-projectPath", project,
        "-executeMethod", "BuildWebGL.PerformBuild",
        "-buildTarget", "WebGL",
        "-logFile", log_path,
    ]
    print("[*] 启动 Unity 构建（可能耗时数分钟至数十分钟）...")
    print("    ", " ".join(cmd))
    t0 = time.time()
    proc = subprocess.run(cmd)
    print("[*] Unity 退出码:", proc.returncode, "  耗时 %.1f 分钟" % ((time.time() - t0) / 60))
    return proc.returncode


def collect_build(src_build_dir, out_dir, company, product):
    # src_build_dir: <project>/Builds/WebGL
    os.makedirs(out_dir, exist_ok=True)
    # 复制整个 WebGL 输出（Build/ + TemplateData/ + StreamingAssets 等）
    for name in os.listdir(src_build_dir):
        s = os.path.join(src_build_dir, name)
        d = os.path.join(out_dir, name)
        if os.path.isdir(s):
            if os.path.exists(d):
                shutil.rmtree(d)
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)

    build_sub = os.path.join(out_dir, "Build")
    if not os.path.isdir(build_sub):
        # 有些模板直接把文件平铺在输出根
        build_sub = out_dir

    def find(ext):
        for f in os.listdir(build_sub):
            if f.lower().endswith(ext.lower()):
                return f
        return None

    loader = find(".loader.js")
    data = find(".data")
    framework = find(".framework.js")
    code = find(".wasm")

    if not (loader and data and framework and code):
        raise RuntimeError(
            "未在 %s 找到完整构建产物 (.loader.js/.data/.framework.js/.wasm)。"
            "请检查 Unity 构建日志。" % build_sub)

    # 路径相对于 game/index.html 所在目录（即 out_dir 的父目录）
    def rel(fn):
        return "build/" + fn

    cfg = {
        "loaderUrl": rel(loader),
        "dataUrl": rel(data),
        "frameworkUrl": rel(framework),
        "codeUrl": rel(code),
        "streamingAssetsUrl": "build/StreamingAssets",
        "companyName": company,
        "productName": product,
        "webglContextAttributes": CONTEXT_ATTRS,
    }
    with open(os.path.join(out_dir, "build.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("[*] 已生成 build.json:")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unity", required=True, help="Unity 6000.0.35f1 Editor 的 Unity.exe 路径")
    ap.add_argument("--project", required=True, help="AssetRipper 导出的 Unity 工程根目录")
    ap.add_argument("--out", default="game/build", help="WebGL 产物输出目录（默认 game/build）")
    ap.add_argument("--company", default="DaXuanBa")
    ap.add_argument("--product", default="Get To Work")
    args = ap.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = args.out if os.path.isabs(args.out) else os.path.join(repo_root, args.out)
    log_path = os.path.join(repo_root, "tools", "unity_build.log")
    src_build = os.path.join(args.project, "Builds", "WebGL")

    if run_unity(args.unity, args.project, log_path) != 0:
        print("[!] Unity 构建失败，详见日志:", log_path)
        sys.exit(1)

    if not os.path.isdir(src_build):
        print("[!] 未找到构建输出:", src_build)
        sys.exit(1)

    collect_build(src_build, out_dir, args.company, args.product)
    print("[*] 完成。把 game/ 推送到 GitHub 即可上线。")


if __name__ == "__main__":
    main()
