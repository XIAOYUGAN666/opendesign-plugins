// Actual compiled Bridge + exact SDK; disposable headless documents, no user canvas/Host lease.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Web.Script.Serialization;
using Grasshopper;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Parameters;
using Grasshopper.Kernel.Types;
using Rhino;
using Rhino.Runtime.InProcess;

internal static class NativeGhValueSdkProbe
{
    static string sdkRoot;
    static string nativeRoot;
    static Assembly bridge;

    [STAThread]
    static int Main(string[] args)
    {
        if (args.Length != 2) return 2;
        sdkRoot = Path.GetFullPath(args[0]);
        nativeRoot = Path.GetFullPath(args[1]);
        AppDomain.CurrentDomain.AssemblyResolve += delegate(object sender, ResolveEventArgs request)
        {
            foreach (string directory in new[] { sdkRoot, Path.Combine(sdkRoot, "..", "Plug-ins", "Grasshopper"), nativeRoot })
            {
                string path = Path.Combine(directory, new AssemblyName(request.Name).Name + ".dll");
                if (File.Exists(path)) return Assembly.LoadFrom(path);
            }
            return null;
        };
        try { return Run(); }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    static int Run()
    {
        bridge = Assembly.LoadFrom(Path.Combine(nativeRoot, "RhinoMcpBridge.rhp"));
        using (var core = new RhinoCore(new[] { "/NOSPLASH" }, WindowStyle.NoWindow))
        {
            if (RhinoApp.Version.ToString() != "8.26.25349.19001") throw new InvalidOperationException("Exact accepted Rhino build required.");
            using (var rhino = RhinoDoc.CreateHeadless(null))
            using (var document = new GH_Document())
            {
                var sphere = (GH_Component)Instances.ComponentServer.EmitObjectProxy(new Guid("dabc854d-f50e-408a-b001-d043c7de151d")).CreateInstance();
                sphere.CreateAttributes();
                if (!document.AddObject(sphere, false)) throw new InvalidOperationException("Disposable component creation failed.");
                Guid id = sphere.InstanceGuid;
                var baseline = Values(document, id);
                document.IsModified = false;
                Set(rhino, document, id, "5");
                var first = Values(document, id);
                bool successfulSetModified = document.IsModified;
                Set(rhino, document, id, "7");
                var second = Values(document, id);
                int previousUndo = document.UndoServer.UndoCount;
                document.UndoServer.PerformUndo();
                bool undone = document.UndoServer.UndoCount == previousUndo - 1;
                var restored = Values(document, id);
                Set(rhino, document, id, "[2,3]");
                var list = Values(document, id);
                document.IsModified = false;
                string invalidError = null;
                try { Set(rhino, document, id, "\"not-a-number\""); }
                catch (Exception error)
                {
                    while (error is TargetInvocationException && error.InnerException != null) error = error.InnerException;
                    invalidError = error.Message;
                }
                var afterInvalid = Values(document, id);
                bool failedCleanSetModified = document.IsModified;
                document.IsModified = true;
                string mixedError = null;
                try { Set(rhino, document, id, "[4,\"not-a-number\"]"); }
                catch (Exception error)
                {
                    while (error is TargetInvocationException && error.InnerException != null) error = error.InnerException;
                    mixedError = error.Message;
                }
                var afterMixed = Values(document, id);
                bool failedDirtySetModified = document.IsModified;
                Set(rhino, document, id, "[]");
                var cleared = Values(document, id);
                Console.WriteLine("RESULT_JSON=" + new JavaScriptSerializer().Serialize(new {
                    softwareBuild = RhinoApp.Version.ToString(), baseline, first, second, undone, restored, list,
                    invalidError, afterInvalid, mixedError, afterMixed, cleared,
                    successfulSetModified, failedCleanSetModified, failedDirtySetModified,
                    rhinoObjectCount = rhino.Objects.Count, ghObjectCount = document.ObjectCount
                }));
            }
        }
        return 0;
    }

    static double[] Values(GH_Document document, Guid id)
    {
        var sphere = (GH_Component)document.Objects.Single(item => item.InstanceGuid == id);
        return ((Param_Number)sphere.Params.Input[1]).PersistentData.AllData(true).Cast<GH_Number>().Select(value => value.Value).ToArray();
    }

    static void Set(RhinoDoc rhino, GH_Document document, Guid id, string value)
    {
        var json = Assembly.LoadFrom(Path.Combine(nativeRoot, "System.Text.Json.dll"));
        var documentType = json.GetType("System.Text.Json.JsonDocument", true);
        var optionsType = json.GetType("System.Text.Json.JsonDocumentOptions", true);
        var parsed = documentType.GetMethod("Parse", new[] { typeof(string), optionsType }).Invoke(null, new[] {
            (object)("{\"instance_id\":\""+id+"\",\"input_index\":1,\"value\":"+value+"}"), Activator.CreateInstance(optionsType)
        });
        try
        {
            var contextType = bridge.GetType("RhinoMcpBridge.RequestContext", true);
            var constructor = contextType.GetConstructors(BindingFlags.Instance|BindingFlags.Public|BindingFlags.NonPublic).Single(item => item.GetParameters().Length == 8);
            var context = constructor.Invoke(new object[] { rhino, document, null, .001, .017, rhino.ModelUnitSystem, "sdk-gh-value", CancellationToken.None });
            bridge.GetType("RhinoMcpBridge.Functions.GrasshopperParameters", true).GetMethod("SetParameterValue", BindingFlags.Static|BindingFlags.NonPublic).Invoke(null, new[] { context, documentType.GetProperty("RootElement").GetValue(parsed, null) });
        }
        finally { ((IDisposable)parsed).Dispose(); }
    }
}
