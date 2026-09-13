using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;

// ---------------------------------------------------------------------------
// UnityLinker shim for the "Get To Work" WebGL port.
//
// WHY THIS EXISTS
// ---------------
// The Unity 6000.0.35f1 install on this machine has an INCOMPLETE WebGL support
// module. The platform engine-module directory that UnityLinker's
// EngineModuleResolver reads is missing, so every IL2CPP build dies with:
//
//   Fatal error in Unity CIL Linker
//   Mono.Cecil.AssemblyResolutionException: Failed to resolve assembly:
//     'UnityEngine.SharedInternalsModule, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null'
//      at Unity.Linker.EngineStripping.EngineModuleResolver.ResolveModuleAssembly(...)
//
// The official fix is reinstalling the WebGL module, but
// download.unity3d.com redirects to download.unitychina.cn which returns
// 404 NoSuchKey for every TargetSupportInstaller, so that is not available.
//
// WHAT IT DOES
// ------------
// Only when invoked with a response file (`@...rsp`, i.e. a real link run):
//   * adds  --disable-engine-module-support          (skip the module table lookup)
//   * adds  --allowed-assembly=<each UnityEngine.*.dll>  (so engine types still resolve)
// Everything else is passed through verbatim to UnityLinker.real.exe and its
// exit code is forwarded, so Bee sees exactly what it expects.
// ---------------------------------------------------------------------------
class GTWLinkerShim
{
    static string Quote(string s)
    {
        if (s.Length > 0 && s.IndexOf(' ') < 0 && s.IndexOf('"') < 0 && s.IndexOf('\t') < 0)
            return s;
        var sb = new StringBuilder();
        sb.Append('"');
        int backslashes = 0;
        foreach (char c in s)
        {
            if (c == '\\') { backslashes++; sb.Append(c); continue; }
            if (c == '"')
            {
                sb.Append(new string('\\', backslashes + 1));
                sb.Append('"');
                backslashes = 0;
                continue;
            }
            backslashes = 0;
            sb.Append(c);
        }
        if (backslashes > 0) sb.Append(new string('\\', backslashes));
        sb.Append('"');
        return sb.ToString();
    }

    static int Main(string[] args)
    {
        string selfDir = Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location);
        string real = Path.Combine(selfDir, "UnityLinker.real.exe");
        if (!File.Exists(real))
        {
            Console.Error.WriteLine("[gtw-shim] missing " + real + " (original linker backup)");
            return 2;
        }

        // <deploy>/../../../Managed/UnityEngine  ==  Editor/Data/Managed/UnityEngine
        string engineDir = Environment.GetEnvironmentVariable("GTW_ENGINE_MODULES");
        if (string.IsNullOrEmpty(engineDir))
            engineDir = Path.GetFullPath(Path.Combine(selfDir, "..", "..", "..", "Managed", "UnityEngine"));

        bool hasRsp = false;
        bool alreadyDisabled = false;
        foreach (string a in args)
        {
            if (a.StartsWith("@", StringComparison.Ordinal)) hasRsp = true;
            if (a.StartsWith("--disable-engine-module-support", StringComparison.Ordinal)) alreadyDisabled = true;
        }

        var extra = new List<string>();
        if (hasRsp)
        {
            if (!alreadyDisabled) extra.Add("--disable-engine-module-support");
            if (Directory.Exists(engineDir))
            {
                string[] files = Directory.GetFiles(engineDir, "UnityEngine*.dll");
                Array.Sort(files, StringComparer.OrdinalIgnoreCase);
                foreach (string f in files) extra.Add("--allowed-assembly=" + f);
            }
            else
            {
                Console.Error.WriteLine("[gtw-shim] engine module folder not found: " + engineDir);
            }
        }

        var line = new StringBuilder();
        foreach (string a in args) { line.Append(Quote(a)); line.Append(' '); }
        foreach (string e in extra) { line.Append(Quote(e)); line.Append(' '); }

        var psi = new ProcessStartInfo();
        psi.FileName = real;
        psi.Arguments = line.ToString();
        psi.UseShellExecute = false;                 // inherit stdout/stderr handles
        psi.WorkingDirectory = Directory.GetCurrentDirectory();

        try
        {
            using (Process p = Process.Start(psi))
            {
                p.WaitForExit();
                return p.ExitCode;
            }
        }
        catch (Exception e)
        {
            Console.Error.WriteLine("[gtw-shim] failed to run linker: " + e);
            return 3;
        }
    }
}
