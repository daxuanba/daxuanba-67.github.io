# Get To Work · WebGL 构建流水线

> 目标：把 Steam 上的 PC 版（Unity 6000.0.35f1 / IL2CPP）变成网页可玩版，
> 产物丢进 `game/build/` 后，站点进入即自动加载开玩。

## 为什么需要这一步

浏览器跑不了 `.exe`。必须先用 Unity **重新编译成 WebGL**。
本仓库页面（`index.html` → `game/index.html`）已做成 WebGL 就绪：
检测到 `game/build.json` 就自动启动 Unity 实例，否则显示游戏启动器。

## 前置条件（关键）

- **Unity 6000.0.35f1 Editor**（changeset `9a3bc604008a`）。
  - 必须用同源版本，跨大版本升级 Unity 6 工程极易损坏 IL2CPP 反编译产物。
  - 安装时勾选 **WebGL Build Support** 模块。
  - 国内镜像 `download.unitychina.cn` 目前**没有** 6000.x 任何版本
    （实测 `NoSuchKey` 404），需在能正常下载 Unity 6 的网络/机器上获取。
- **AssetRipper 2.x**（https://github.com/AssetRipper/AssetRipper/releases）。
- Windows 机器（或 macOS/Linux，对应改路径即可）。

## 步骤

### 1. 反编译游戏为 Unity 工程
```
AssetRipper.exe "<Steam>/steamapps/common/Get To Work" -o "D:/gtw_export"
```
- 选 **DLL 模式**（保留脚本逻辑，不尝试反编译 C# 到源码，最稳）。
- 导出后得到 `D:/gtw_export/GameAssembly/` 与 `D:/gtw_export/Project/`。
- 用 Unity 6000.0.35f1 打开 `Project/`。

### 2. 工程内处理（常踩的坑）
- **FMOD / 其他原生音频插件**：WebGL 不支持原生动态库，通常要换成 Unity 自带
  `UnityWebGLAudio` / `WebAudio`，或临时禁用 FMOD 用 `AudioSource` 兜底。
- **Steamworks / Rewired / 原生输入**：WebGL 无 Steam API，需加 `#if UNITY_WEBGL` 编译分支
  把这些调用短路，否则构建报错或运行崩溃。
- **Addressables / 资源包**：确认 Bundle 能打进 WebGL（Compression 用 `WebGL` 兼容方式）。
- **Player Settings → WebGL**：
  - Publishing Settings：Decompression Fallback 勾上
  - Memory Growth / 初始堆按需调大
  - 关闭不必要的 Code Optimization（首包体积）

### 3. 一键命令行构建 + 生成 build.json
用本目录 `build_webgl.py`：
```
python tools/build_webgl.py \
  --unity "C:/Program Files/Unity 6000.0.35f1/Editor/Unity.exe" \
  --project "D:/gtw_export/Project" \
  --out "game/build"
```
脚本会：
1. `Unity -batchmode -buildTarget WebGL -executeMethod ...` 产出 `Build/` 文件夹
2. 把 `Build/` 复制到 `game/build/`
3. 依据产物自动生成 `game/build.json`（含 `loaderUrl` / `dataUrl` / `frameworkUrl` / `codeUrl` / `streamingAssetsUrl` / `companyName` / `productName` / `webglContextAttributes`）

> 若你的 WebGL 模板不是默认 `Build/` 命名，按脚本注释里的 `BUILD_SUBDIR` 调整。

### 4. 上线
```
git add game/build game/build.json
git commit -m "feat: add WebGL build"
git push
```
推送后 `https://daxuanba.github.io/daxuanba-67.github.io/` 进入即自动加载游戏。

## 排错
- 构建卡在 `il2cpp`：确认装了 **Windows IL2CPP** 模块，或改用 Mono（WebGL 仅 IL2CPP，必装）。
- `createUnityInstance is not defined`：build.json 的 `loaderUrl` 指向的 `.loader.js` 未随包上传，重新检查复制步骤。
- 黑屏但有日志：开浏览器控制台看 WASM 报错，多半是插件/反射在 WebGL 不支持。
