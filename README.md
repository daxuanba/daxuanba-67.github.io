# daxuanba-67.github.io

Get To Work 网页版。打开首页即自动加载游戏。

在线地址：https://daxuanba.github.io/daxuanba-67.github.io/

> 当前状态：页面已上线；若 `game/` 下没有 WebGL 构建，会自动降级为「游戏启动器」界面。
> WebGL 原版移植的可行性评估见仓库内 `WebGL移植报告.md`（工作区）。

## 目录结构

```
index.html              首页外壳：全屏容器 + 加载进度 + 全屏/重载按钮
game/index.html         Unity WebGL 宿主页（读 build.json 启动；缺失时显示启动器）
game/unity-loader.js    分片重组加载器：下载分片 → Blob URL → 喂 Unity
game/build/             Unity WebGL 产物（拆分后的分片 + build.json）
assets/                 封面、图标、分享图
tools/                  构建与打包脚本
```

## 构建管线

```bash
# 1) 工程后处理：生成 Packages/manifest.json + 隔离包提供的重复 DLL
python tools/prepare_unity_project.py \
    --project "D:/GetToWorkUnity/ExportedProject" \
    --unity-dir "D:/Unity/6000.0.35f1"

# 2) 重建 Addressables（AssetRipper 导不出分组配置，这里按「场景名 = 地址」自动重建）
Unity.exe -batchmode -nographics -quit \
    -projectPath "D:/GetToWorkUnity/ExportedProject" \
    -executeMethod GTWAddressables.SetupAndBuildForWebGL
#    脚本源码：tools/unity_editor/GTWAddressables.cs

# 3) 打 WebGL（自动关线程 / 开 JS 解压兜底 / 关代码剥离）
python tools/build_webgl.py --project "D:/GetToWorkUnity/ExportedProject"

# 4) 分片（默认 <95MB/片）+ 生成清单，推到 GitHub Release
python tools/split_package.py --build game/build --site .
```

### 为什么必须分片

GitHub Pages 单站点 1 GB / 单文件 100 MB，而 WebGL 产物（.data/.wasm/资源包）单文件就有几个 GB。
方案：把大文件切成 <95 MB 的分片提交，页面端 `unity-loader.js` 并发下载后在内存里拼回
`Blob`，再以 Blob URL 交给 Unity 加载器。已实测分片重组 SHA256 与原文件完全一致。

## 页面特性

- 打开首页即加载游戏，无需二次点击
- 加载进度条（Unity 真实进度通过 postMessage 上报）
- 全屏按钮、重载按钮
- 竖屏设备提示横屏
- 深色主题

## 本地预览

```bash
python -m http.server 8000
# 打开 http://localhost:8000
```

## 踩坑（复用价值高）

1. **AssetRipper 导出期间不要读它的日志文件**。MSYS 的 `cat/grep/tail` 打开句柄不带
   `FILE_SHARE_WRITE`，会让 AssetRipper 的 `File.AppendAllText` 抛
   `UnauthorizedAccessException`，整个导出请求 500 中断。
2. **托管 DLL 不要从 `Assets/Plugins` 移走**。它们能正常编译，只在运行时才可能因缺原生库出错；
   误移会导致反编译代码编译失败（例如移掉 `_Isto.Core.Xbox.dll` → `XBoxGameData.cs` 报错）。
   要移的只有**包提供的重复程序集**（`Unity.Addressables.dll`、`Unity.RenderPipelines.*` 等）。
3. **Unity 自带包版本是权威来源**：读
   `<Editor>/Data/Resources/PackageManager/BuiltInPackages/*/package.json` 与 `Editor/*.tgz`，
   不要依赖 `packages.unity.com`（它的 URP 只到 10.10.1，Unity 6 的 17.x 不在那个源）。
