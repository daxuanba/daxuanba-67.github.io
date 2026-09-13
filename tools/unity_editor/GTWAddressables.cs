// GTWAddressables.cs — 自动重建 Addressables 分组并构建 WebGL 资源包
//
// 背景：AssetRipper 无法导出 Addressables 分组配置（编辑器资产，不随游戏发布），
//       但游戏只用 Addressables 做 `Addressables.LoadSceneAsync(sceneName)`，
//       地址就是场景文件名。因此可以把所有关卡场景按「场景名 = 地址」自动注册，
//       为 WebGL 重新打包。
//
// 用法（Unity 批处理）：
//   Unity.exe -batchmode -nographics -quit -projectPath <proj> \
//       -executeMethod GTWAddressables.SetupAndBuildForWebGL

using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.AddressableAssets;
using UnityEditor.AddressableAssets.Build;
using UnityEditor.AddressableAssets.Settings;
using UnityEngine;

public static class GTWAddressables
{
    const string GroupName = "GTW_Scenes";

    /// <summary>扫描 Assets/Scenes 下所有场景，注册为可寻址资源，地址 = 场景文件名。</summary>
    [MenuItem("GTW/1. Setup Addressables Groups")]
    public static void Setup()
    {
        var settings = AddressableAssetSettingsDefaultObject.Settings;
        if (settings == null)
        {
            settings = AddressableAssetSettingsDefaultObject.GetSettings(true);
            Debug.Log("[GTW] 新建 AddressableAssetSettings");
        }

        var group = settings.FindGroup(GroupName);
        if (group == null)
            group = settings.CreateGroup(GroupName, false, false, false, null);

        // 单组内同名地址会冲突，先把旧条目清掉重建，保证幂等
        var toRemove = group.entries.Select(e => e.guid).ToList();
        foreach (var g in toRemove) settings.RemoveAssetEntry(g, false);

        var guids = AssetDatabase.FindAssets("t:Scene", new[] { "Assets/Scenes" });
        int count = 0;
        foreach (var guid in guids)
        {
            var path = AssetDatabase.GUIDToAssetPath(guid);
            if (string.IsNullOrEmpty(path)) continue;
            var entry = settings.CreateOrMoveEntry(guid, group, false, false);
            if (entry == null) continue;
            entry.address = Path.GetFileNameWithoutExtension(path);
            count++;
        }

        settings.SetDirty(AddressableAssetSettings.ModificationEvent.BatchModification, null, true, true);
        AssetDatabase.SaveAssets();
        Debug.Log("[GTW] 已注册 " + count + " 个场景为可寻址资源（地址=场景名）");
    }

    /// <summary>为当前激活平台构建 Addressables 内容。</summary>
    [MenuItem("GTW/2. Build Addressables Content")]
    public static void BuildContent()
    {
        AddressablesPlayerBuildResult result;
        AddressableAssetSettings.BuildPlayerContent(out result);
        if (result != null && !string.IsNullOrEmpty(result.Error))
        {
            Debug.LogError("[GTW] Addressables 构建失败: " + result.Error);
            if (Application.isBatchMode) EditorApplication.Exit(1);
            return;
        }
        Debug.Log("[GTW] Addressables 构建完成，耗时 " + (result != null ? result.Duration.ToString("F1") : "?") + "s");
    }

    /// <summary>批处理入口：切 WebGL → 建组 → 打包。</summary>
    public static void SetupAndBuildForWebGL()
    {
        try
        {
            if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.WebGL)
            {
                Debug.Log("[GTW] 切换到 WebGL 平台…");
                EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.WebGL, BuildTarget.WebGL);
            }
            Setup();
            BuildContent();
            Debug.Log("[GTW] 全部完成");
            if (Application.isBatchMode) EditorApplication.Exit(0);
        }
        catch (System.Exception e)
        {
            Debug.LogError("[GTW] 异常: " + e);
            if (Application.isBatchMode) EditorApplication.Exit(1);
        }
    }

    /// <summary>打印现有分组概览，便于排查。</summary>
    [MenuItem("GTW/0. Report Groups")]
    public static void Report()
    {
        var settings = AddressableAssetSettingsDefaultObject.Settings;
        if (settings == null) { Debug.Log("[GTW] 尚无 AddressableAssetSettings"); return; }
        foreach (var g in settings.groups)
        {
            if (g == null) continue;
            Debug.Log("[GTW] 组 " + g.Name + " 条目数=" + g.entries.Count);
        }
        Debug.Log("[GTW] 默认组: " + (settings.DefaultGroup != null ? settings.DefaultGroup.Name : "null"));
    }
}
