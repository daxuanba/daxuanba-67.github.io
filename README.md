# daxuanba-67.github.io

Get To Work 网页版。打开首页即自动加载游戏（Unity WebGL）。

在线地址：https://daxuanba-67.github.io

## 目录结构

```
index.html          首页外壳：全屏容器 + 加载进度 + 全屏/重载按钮
game/index.html     Unity WebGL 宿主页（自动读取 build.json 启动游戏）
game/Build/         Unity WebGL 构建产物（.loader.js / .data / .framework.js / .wasm）
game/build.json     构建清单，由 tools/gen-build-json.py 生成
assets/             封面、图标
tools/              辅助脚本
```

## 放入游戏

1. Unity 切到 WebGL 平台，`Compression Format` 选 Gzip 或 Disabled，构建输出到任意目录。
2. 把构建结果里的 `Build/` 整目录复制到本仓库的 `game/` 下。
3. 生成清单：

   ```bash
   python tools/gen-build-json.py
   ```

4. 提交推送，GitHub Pages 会自动发布。

如果直接用 Unity 自带的 `index.html` 覆盖 `game/index.html` 也能跑，只是外壳进度条会退化为模拟进度。

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
