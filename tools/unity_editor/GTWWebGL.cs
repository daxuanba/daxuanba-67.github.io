// GTWWebGL.cs — Get To Work 的 WebGL 批处理构建入口（由 daxuanba-67.github.io 管线注入）
//
// 用法：
//   Unity.exe -batchmode -nographics -quit -projectPath <proj> -buildTarget WebGL \
//       -executeMethod GTWWebGL.PerformBuild  -logFile <log>       # 仅播放器（EditorBuildSettings 场景）
//   Unity.exe ... -executeMethod GTWWebGL.PerformFullBuild         # 重建 Addressables + 播放器
//   Unity.exe ... -executeMethod GTWWebGL.SwitchOnly               # 只切平台

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.AddressableAssets;
using UnityEditor.AddressableAssets.Build;
using UnityEditor.AddressableAssets.Settings;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEngine;

public static class GTWWebGL
{
    const string OutDir = "Builds/WebGL";
    const string SceneGroupName = "GTW_Scenes";

    // ------------------------------------------------------------------ 平台
    public static void SwitchToWebGL()
    {
        if (EditorUserBuildSettings.activeBuildTarget == BuildTarget.WebGL)
        {
            Debug.Log("[GTW] 已在 WebGL 平台");
            return;
        }
        Debug.Log("[GTW] 切换平台 -> WebGL (可能需要较长时间重新导入资源) ...");
        bool ok = EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.WebGL, BuildTarget.WebGL);
        Debug.Log("[GTW] 切换结果: " + ok + " / 当前平台: " + EditorUserBuildSettings.activeBuildTarget);
        if (!ok) throw new Exception("切换到 WebGL 平台失败");
    }

    // ------------------------------------------------------------------ 编译宏
    /// <summary>
    /// 追加一个脚本编译宏（不清空原有宏）。
    /// 用途：com.unity.collections 2.5.1 的 NativeList.AsReadOnly 在「未定义
    /// ENABLE_UNITY_COLLECTIONS_CHECKS」时走 2 参数 NativeArray&lt;T&gt;.ReadOnly 构造，
    /// 而 Unity 6000.0.35f1 的引擎只暴露 3 参数(带 ref AtomicSafetyHandle)版本 ->
    /// CS7036 编译失败。补上这个宏即可让包走 3 参数分支。
    /// </summary>
    static void AddDefine(NamedBuildTarget target, string symbol)
    {
        var cur = PlayerSettings.GetScriptingDefineSymbols(target) ?? "";
        var list = new List<string>(cur.Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries));
        if (list.Contains(symbol))
        {
            Debug.Log("[GTW] 宏已存在: " + symbol);
            return;
        }
        list.Add(symbol);
        PlayerSettings.SetScriptingDefineSymbols(target, string.Join(";", list));
        Debug.Log("[GTW] 追加宏: " + symbol + "  => " + string.Join(";", list));
    }

    // ------------------------------------------------------------------ 设置
    static void ApplyPlayerSettings()
    {
        Debug.Log("[GTW] 现有宏(WebGL): " + PlayerSettings.GetScriptingDefineSymbols(NamedBuildTarget.WebGL));

        PlayerSettings.companyName = "DaXuanBa";
        PlayerSettings.productName = "Get To Work";
        PlayerSettings.runInBackground = true;
        PlayerSettings.defaultWebScreenWidth = 1280;
        PlayerSettings.defaultWebScreenHeight = 720;
        PlayerSettings.defaultScreenWidth = 1280;
        PlayerSettings.defaultScreenHeight = 720;

        // GitHub Pages 无法下发 COOP/COEP -> 必须关线程
        PlayerSettings.WebGL.threadsSupport = false;
        // 静态托管没有 Content-Encoding 头 -> 必须开 JS 解压兜底
        PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Brotli;
        PlayerSettings.WebGL.decompressionFallback = true;
        PlayerSettings.WebGL.dataCaching = true;
        PlayerSettings.WebGL.template = "APPLICATION:Default";
        PlayerSettings.WebGL.emscriptenArgs = "";
        PlayerSettings.WebGL.powerPreference = WebGLPowerPreference.HighPerformance;

        // AssetRipper 导出的 ProjectSettings 里 WebGL 内存全是 0 ->
        // emcc 收到 "-s INITIAL_MEMORY=0" 直接报
        //   "INITIAL_MEMORY must be larger than STACK_SIZE, was 0 (STACK_SIZE=524288)"
        // 这里显式给出「初始 512MB / 上限 2048MB / 几何增长」。
        try
        {
            PlayerSettings.WebGL.memorySize = 512;                              // 兼容旧字段(None 模式用)
            PlayerSettings.WebGL.initialMemorySize = 512;                       // 初始堆 MB
            PlayerSettings.WebGL.maximumMemorySize = 2048;                      // 上限 MB
            PlayerSettings.WebGL.memoryGrowthMode = UnityEditor.WebGLMemoryGrowthMode.Geometric;
            PlayerSettings.WebGL.geometricMemoryGrowthStep = 0.2f;              // 每次 +20%
            PlayerSettings.WebGL.memoryGeometricGrowthCap = 2048;
        }
        catch (Exception e)
        {
            Debug.LogWarning("[GTW] WebGL 内存 API 设置失败(将回退到 ProjectSettings.asset): " + e.Message);
        }
        Debug.Log(string.Format("[GTW] WebGL 内存: memorySize={0} initial={1} max={2} growth={3} cap={4}",
            PlayerSettings.WebGL.memorySize,
            PlayerSettings.WebGL.initialMemorySize,
            PlayerSettings.WebGL.maximumMemorySize,
            PlayerSettings.WebGL.memoryGrowthMode,
            PlayerSettings.WebGL.memoryGeometricGrowthCap));

        // 反编译工程大量使用反射（Zenject/Odin/BehaviorDesigner/Rewired）-> 关闭代码剥离
        PlayerSettings.SetScriptingBackend(NamedBuildTarget.WebGL, ScriptingImplementation.IL2CPP);
        PlayerSettings.SetManagedStrippingLevel(NamedBuildTarget.WebGL, ManagedStrippingLevel.Disabled);
        PlayerSettings.SetIl2CppCompilerConfiguration(NamedBuildTarget.WebGL, Il2CppCompilerConfiguration.Release);
        PlayerSettings.SetIl2CppCodeGeneration(NamedBuildTarget.WebGL, Il2CppCodeGeneration.OptimizeSize);

        AddDefine(NamedBuildTarget.WebGL, "ENABLE_UNITY_COLLECTIONS_CHECKS");

        AssetDatabase.SaveAssets();
        Debug.Log("[GTW] PlayerSettings 已应用");
    }

    // ------------------------------------------------------------------ Addressables
    [MenuItem("GTW/Setup Addressables")]
    public static void SetupAddressables()
    {
        var settings = AddressableAssetSettingsDefaultObject.Settings;
        if (settings == null)
        {
            settings = AddressableAssetSettingsDefaultObject.GetSettings(true);
            Debug.Log("[GTW] 创建 AddressableAssetSettings");
        }

        var group = settings.FindGroup(SceneGroupName);
        if (group == null)
            group = settings.CreateGroup(SceneGroupName, false, false, false, null);

        // 幂等：先清空该组
        foreach (var guid in group.entries.Select(e => e.guid).ToList())
            settings.RemoveAssetEntry(guid, false);

        int n = 0;
        foreach (var guid in AssetDatabase.FindAssets("t:Scene", new[] { "Assets/Scenes" }))
        {
            var path = AssetDatabase.GUIDToAssetPath(guid);
            if (string.IsNullOrEmpty(path)) continue;
            var entry = settings.CreateOrMoveEntry(guid, group, false, false);
            if (entry == null) continue;
            entry.address = Path.GetFileNameWithoutExtension(path);   // 地址 = 场景文件名
            n++;
        }

        settings.SetDirty(AddressableAssetSettings.ModificationEvent.BatchModification, null, true, true);
        AssetDatabase.SaveAssets();
        Debug.Log("[GTW] 已注册 " + n + " 个场景为可寻址资源");
    }

    public static void BuildAddressables()
    {
        AddressablesPlayerBuildResult result;
        AddressableAssetSettings.BuildPlayerContent(out result);
        if (result != null && !string.IsNullOrEmpty(result.Error))
            throw new Exception("Addressables 构建失败: " + result.Error);
        Debug.Log("[GTW] Addressables 构建完成, 耗时 " +
                  (result != null ? result.Duration.ToString("F1") : "?") + "s");
    }

    // ------------------------------------------------------------------ 打包
    static void DoBuild(bool useAllScenes)
    {
        var scenes = new List<string>();
        if (useAllScenes)
        {
            foreach (var guid in AssetDatabase.FindAssets("t:Scene"))
                scenes.Add(AssetDatabase.GUIDToAssetPath(guid));
            scenes = scenes.Where(p => !string.IsNullOrEmpty(p)).OrderBy(p => p).ToList();
        }
        else
        {
            foreach (var s in EditorBuildSettings.scenes)
                if (s.enabled && !string.IsNullOrEmpty(s.path)) scenes.Add(s.path);
        }

        Debug.Log("[GTW] 打包场景数: " + scenes.Count);
        foreach (var s in scenes) Debug.Log("    " + s);

        if (scenes.Count == 0) throw new Exception("没有可用场景");

        if (Directory.Exists(OutDir)) Directory.Delete(OutDir, true);
        Directory.CreateDirectory(OutDir);

        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = scenes.ToArray(),
            locationPathName = OutDir,
            target = BuildTarget.WebGL,
            options = BuildOptions.None,
        });

        var sum = report.summary;
        Debug.Log(string.Format("[GTW] 构建结果={0} 大小={1:F1}MB 错误={2} 警告={3} 耗时={4}",
            sum.result, sum.totalSize / 1048576.0, sum.totalErrors, sum.totalWarnings, sum.totalTime));

        if (sum.result != BuildResult.Succeeded)
        {
            foreach (var step in report.steps)
                foreach (var m in step.messages)
                    if (m.type == LogType.Error || m.type == LogType.Exception)
                        Debug.LogError("[GTW][ERR] " + m.content);
            throw new Exception("构建失败: " + sum.result);
        }
    }

    // ------------------------------------------------------------------ 桩插件强制重导入
    /// <summary>
    /// 强制 Unity 用 PluginImporter（而非缓存的 DefaultImporter）重新导入
    /// Assets/Plugins/WebGL/libStubRegisters.a，使其被链接进 WebGL 播放器。
    /// 该 .a 为 6000.0.35f1 WebGL 引擎未实现的框架/编辑器 icall 注册函数提供空桩，
    /// 满足链接器 (wasm-ld) 对 Register_* 符号的需求。
    /// </summary>
    static void ForceImportStubPlugin()
    {
        string[] candidates = {
            "Assets/Plugins/WebGL/libStubRegisters.a",
            "Assets/Plugins/WebGL/StubRegisters.cpp",
        };
        bool found = false;
        foreach (var stub in candidates)
        {
            if (System.IO.File.Exists(stub))
            {
                found = true;
                Debug.Log("[GTW] 强制重导入桩插件: " + stub);
                AssetDatabase.ImportAsset(stub, ImportAssetOptions.ForceUpdate | ImportAssetOptions.DontDownloadFromCacheServer);
                var imp = AssetImporter.GetAtPath(stub);
                Debug.Log("[GTW] 桩插件导入器类型: " + (imp != null ? imp.GetType().FullName : "NULL"));
            }
        }
        if (!found)
            Debug.LogWarning("[GTW] 桩插件不存在，跳过: " + string.Join(", ", candidates));
    }

    // ------------------------------------------------------------------ 入口
    public static void SwitchOnly()
    {
        try { SwitchToWebGL(); Exit(0); }
        catch (Exception e) { Debug.LogError("[GTW] " + e); Exit(1); }
    }

    // 快速探针：只导入桩插件并打印导入器类型，用于验证 PluginImporter 是否生效
    public static void ProbeStubImporter()
    {
        try { ForceImportStubPlugin(); Exit(0); }
        catch (Exception e) { Debug.LogError("[GTW] " + e); Exit(1); }
    }

    public static void PerformBuild()
    {
        try
        {
            SwitchToWebGL();
            ApplyPlayerSettings();
            ForceImportStubPlugin();
            DoBuild(false);          // 仅 EditorBuildSettings 里的引导场景（快速验证）
            Exit(0);
        }
        catch (Exception e) { Debug.LogError("[GTW] EXCEPTION: " + e); Exit(1); }
    }

    public static void PerformFullBuild()
    {
        try
        {
            SwitchToWebGL();
            ApplyPlayerSettings();
            SetupAddressables();
            BuildAddressables();
            ForceImportStubPlugin();
            DoBuild(false);
            Exit(0);
        }
        catch (Exception e) { Debug.LogError("[GTW] EXCEPTION: " + e); Exit(1); }
    }

    static void Exit(int code)
    {
        if (Application.isBatchMode) EditorApplication.Exit(code);
    }
}
