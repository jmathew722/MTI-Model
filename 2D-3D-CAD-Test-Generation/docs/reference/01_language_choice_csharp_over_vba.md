# 01 — Language Choice: Use C# (Standalone .NET), Not VBA

## Recommendation
Write the automation as a **C# standalone console application** that connects to SolidWorks over COM interop. Do not write VBA `.swp` macros.

If a lightweight in-SolidWorks script is ever truly required, prefer a **VSTA C# macro** over VBA. But for this pipeline, standalone C# is the right architecture.

## Why C# over VBA for an AI-generated pipeline

### 1. LLMs write better C# than VBA
- There is vastly more high-quality C# in training data than VBA. Claude's VBA tends to have subtle issues: wrong `Set` usage, late-binding errors that only surface at runtime, `Variant` confusion, array bounds mistakes.
- VBA has no compiler feedback loop that Claude Code can use. A C# project gives **compile-time errors as text** that the agent can read and fix before ever touching SolidWorks. This closes the iteration loop: `dotnet build` → read errors → fix → repeat, all without launching CAD.
- C# is strongly typed. `IModelDoc2`, `IFeatureManager`, `ISketchManager` interfaces catch wrong-argument mistakes at compile time. In VBA, `swModel.FeatureManager.FeatureExtrusion3(...)` with a wrong argument count often just fails silently or throws an opaque COM error at runtime.

### 2. Better engineering ergonomics
- Real error handling (`try/catch` with typed `COMException` and HRESULT inspection) instead of `On Error Resume Next`, which VBA code (and recorded macros) abuse to hide failures.
- Real data structures: the feature plan can be deserialized from JSON (`System.Text.Json`), dimensions kept in dictionaries, geometry in records/structs. VBA collections make this painful.
- Logging, unit tests, CLI arguments, file IO — all trivial in .NET, awkward in VBA.
- Version control friendly: `.cs` files are plain text; `.swp` VBA macros are binary blobs Claude Code cannot diff or edit directly. **This alone disqualifies VBA for an agentic workflow** — the agent must be able to read and edit its own source files.

### 3. Officially supported path
SolidWorks primarily supports VBA, VB.NET, C#, and C++ for API programming, and a .NET standalone application is straightforward to set up — often simpler than trying to script complex logic in the embedded VBA editor.

## When VBA is still acceptable
- **Macro recording for API discovery.** Record an action in the SolidWorks UI, open the recorded VBA, and read it to learn *which* API calls correspond to a UI action and what typical argument values look like. Then reimplement cleanly in C#. Never ship the recorded code.
- Tiny one-off utilities run manually by a human. Not this pipeline.

## Project setup (do this once, reuse for every part)

```bash
dotnet new console -n SwPartBuilder -f net48
```

Use **.NET Framework 4.8** (not .NET 6/8) for maximum COM interop compatibility with SolidWorks, unless you have verified the installed interop assemblies work with modern .NET on the target machine.

Reference the interop DLLs from the SolidWorks install directory (typical path):
```
C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll
C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\api\redist\SolidWorks.Interop.swconst.dll
```
In the `.csproj`, set `<EmbedInteropTypes>false</EmbedInteropTypes>` on these references (embedding interop types causes runtime cast failures with some SolidWorks versions).

## Connecting to SolidWorks from C#

```csharp
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using System;
using System.Runtime.InteropServices;

class Program
{
    static void Main(string[] args)
    {
        ISldWorks swApp = null;
        try
        {
            // Attach to running instance, else start one
            try
            {
                swApp = (ISldWorks)Marshal2.GetActiveObject("SldWorks.Application");
            }
            catch (COMException)
            {
                Type t = Type.GetTypeFromProgID("SldWorks.Application");
                swApp = (ISldWorks)Activator.CreateInstance(t);
            }
            swApp.Visible = true;          // keep visible during development for debuggability
            swApp.UserControl = true;

            // ... build the part ...
        }
        finally
        {
            // Do NOT call swApp.ExitApp() if you attached to a user's session.
            // Release COM objects you created.
        }
    }
}
```
(Note: `Marshal.GetActiveObject` was removed from .NET Core+; on .NET Framework 4.8 it exists as `System.Runtime.InteropServices.Marshal.GetActiveObject`. On modern .NET you need a small `Marshal2` helper using `GetActiveObject` via `oleaut32` — one more reason to stay on Framework 4.8.)

## Iteration workflow the agent should follow
1. Edit `.cs` source.
2. `dotnet build` (or `msbuild`) — fix ALL compiler errors/warnings first.
3. Run against SolidWorks with the target drawing's plan.
4. Read the program's own log output + SolidWorks rebuild errors (doc 07).
5. Fix and repeat. Keep SolidWorks running between iterations (attach, don't restart) — startup is slow.

## Key takeaway
The failure mode you observed (Claude struggling with VBA) is expected. Move to **C# + compile-check loop** and most syntax/type errors disappear before runtime. Reserve VBA recording purely as a documentation tool for discovering which API calls a UI action maps to.
