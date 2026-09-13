#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
断开 AssetRipper 导出工程中「资源 -> 场景对象」的反向引用（导致 Unity 导入死循环）。

现象：Unity 导入时疯狂刷
      Do not use ReadObjectThreaded on scene objects!
  随后段错误崩溃。

原因：场景里有组件字段(mLocalizeTarget / m_Target 等)指向一个被单独导出的资源，
      而该资源又反向引用场景内的对象 -> 循环 -> Unity 线程化读取器死循环。

注意：只有「对象引用」语法才会触发，即 {fileID: <非0>, guid: <场景guid>}。
      形如 `scene: f150cba0...` 的纯 guid 数据字段（OcclusionCullingData）是合法的，不能动。

用法：
  python fix_circular_refs.py                # 试运行(DRY)
  python fix_circular_refs.py --apply        # 实际写入
"""
import os
import re
import sys
import glob

PROJ = os.environ.get("GTW_PROJECT", r"D:\GetToWorkUnity\ExportedProject")
ASSETS = os.path.join(PROJ, "Assets")
SCENE_DIR = os.path.join(ASSETS, "Scenes")
DRY = "--apply" not in sys.argv

# 参与扫描的文本资源后缀（排除 .meta / 场景本身）
EXTS = (".asset", ".prefab", ".mat", ".controller", ".anim", ".playable",
        ".spriteatlas", ".shadergraph", ".vfx", ".overrideController",
        ".mixer", ".fontsettings", ".preset", ".json", ".txt", ".shader")

# 1) 收集场景 guid
scene_guids = set()
for meta in glob.glob(os.path.join(SCENE_DIR, "**", "*.unity.meta"), recursive=True):
    with open(meta, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = re.match(r"\s*guid:\s*([0-9a-fA-F]{32})", line)
            if m:
                scene_guids.add(m.group(1).lower())
                break
print(f"[i] 场景 guid 数: {len(scene_guids)}")

# 2) 只匹配「对象引用」且 fileID != 0
REF_RE = re.compile(
    r"\{fileID:[ \t]*(?!0[ \t]*[,}])(\d+),[ \t]*guid:[ \t]*([0-9a-fA-F]{32}),[ \t]*type:[ \t]*(\d+)\}"
)

targets = []
for root, _dirs, files in os.walk(ASSETS):
    for fn in files:
        if fn.lower().endswith(EXTS) and os.path.splitext(fn)[1].lower() in EXTS:
            targets.append(os.path.join(root, fn))
print(f"[i] 待扫描文本资源: {len(targets)}")

fixed_files, fixed_refs = 0, 0
hit_list = []

for path in targets:
    try:
        with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
            text = f.read()
    except (OSError, UnicodeError):
        continue

    cnt = [0]

    def repl(m):
        fid, guid, typ = m.group(1), m.group(2), m.group(3)
        if guid.lower() in scene_guids:
            cnt[0] += 1
            return "{fileID: 0}"
        return m.group(0)

    new_text = REF_RE.sub(repl, text)
    if cnt[0]:
        if not DRY:
            with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as f:
                f.write(new_text)
        fixed_files += 1
        fixed_refs += cnt[0]
        hit_list.append((path, cnt[0]))

tag = "DRY" if DRY else "+"
print(f"[{tag}] 涉及文件: {fixed_files}，置空引用: {fixed_refs}")
for p, c in hit_list[:40]:
    print(f"    {c}x  {os.path.relpath(p, PROJ)}")
