#!/usr/bin/env python3
"""扫描 game/Build 目录，生成 Unity WebGL 需要的 game/build.json。

用法: python tools/gen-build-json.py [游戏根目录(默认 game)]
Unity 构建产物结构:
    game/Build/xxx.loader.js
    game/Build/xxx.data(.gz)
    game/Build/xxx.framework.js(.gz)
    game/Build/xxx.wasm(.gz)
"""
import json
import os
import sys

root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', 'game')
root = os.path.abspath(root)
build_dir = os.path.join(root, 'Build')

if not os.path.isdir(build_dir):
    sys.exit('未找到 Build 目录: %s' % build_dir)

names = {}
for f in os.listdir(build_dir):
    low = f.lower()
    if low.endswith('.loader.js'):
        names['loader'] = f
    elif low.endswith('.data') or low.endswith('.data.gz'):
        names['data'] = f
    elif low.endswith('.framework.js') or low.endswith('.framework.js.gz'):
        names['framework'] = f
    elif low.endswith('.wasm') or low.endswith('.wasm.gz'):
        names['code'] = f

missing = {'loader', 'data', 'framework', 'code'} - set(names)
if missing:
    sys.exit('Build 目录缺少文件类型: %s' % ', '.join(sorted(missing)))

base = names['loader'][:-len('.loader.js')]
cfg = {
    'companyName': 'Isto',
    'productName': 'Get To Work',
    'productVersion': '1.0',
    'dataUrl': 'Build/' + names['data'],
    'frameworkUrl': 'Build/' + names['framework'],
    'codeUrl': 'Build/' + names['code'],
    'loaderUrl': 'Build/' + names['loader'],
    'streamingAssetsUrl': 'StreamingAssets',
    'symbolsUrl': 'Build/' + base + '.symbols.json' if os.path.exists(os.path.join(build_dir, base + '.symbols.json')) else None,
    'backgroundColor': '#000000',
    'disableWebGL2Fallback': False,
}

out = os.path.join(root, 'build.json')
with open(out, 'w', encoding='utf-8') as fp:
    json.dump(cfg, fp, ensure_ascii=False, indent=2)
print('已生成:', out)
