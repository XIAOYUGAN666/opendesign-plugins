// Test-only probe: actual compiled Bridge against disposable headless Rhino documents.
// It never opens user files, loads the plug-in entry point, or obtains a Host lease.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Web.Script.Serialization;
using Rhino;
using Rhino.DocObjects;
using Rhino.Geometry;
using Rhino.Runtime.InProcess;

internal static class NativeDeleteSdkProbe
{
    static string sdkRoot;
    static string nativeRoot;
    static Assembly bridge;
    static ConstructorInfo contextConstructor;

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
        catch (Exception error)
        {
            Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
            return 1;
        }
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    static int Run()
    {
        bridge = Assembly.LoadFrom(Path.Combine(nativeRoot, "RhinoMcpBridge.rhp"));
        contextConstructor = bridge.GetType("RhinoMcpBridge.RequestContext", true)
            .GetConstructors(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            .Single(item => item.GetParameters().Length == 8);
        var cases = new List<object>();
        using (var core = new RhinoCore(new[] { "/NOSPLASH" }, WindowStyle.NoWindow))
        {
            if (RhinoApp.Version.ToString() != "8.26.25349.19001")
                throw new InvalidOperationException("The exact accepted Rhino build is required.");
            using (var document = RhinoDoc.CreateHeadless(null))
            {
                document.UndoRecordingEnabled = true;
                Guid anchor = document.Objects.AddPoint(new Point3d(1, 2, 3));
                var attributes = document.CreateDefaultAttributes();
                attributes.Name = "SDK_DELETE_TARGET";
                Guid target = document.Objects.AddPoint(new Point3d(4, 5, 6), attributes);
                object result = Invoke(document, "DeleteObject", "{\"id\":\"" + target + "\"}");
                cases.Add(new { kind = "object", result, targetId = target.ToString("D"),
                    targetMissing = !Active(document, target), anchorPreserved = Active(document, anchor),
                    objectCount = ActiveCount(document) });
            }
            using (var document = RhinoDoc.CreateHeadless(null))
            {
                document.UndoRecordingEnabled = true;
                document.Objects.AddPoint(new Point3d(7, 8, 9));
                document.Objects.AddPoint(new Point3d(10, 11, 12));
                object result = Invoke(document, "DeleteObject", "{\"all\":true}");
                cases.Add(new { kind = "all", result, objectCount = ActiveCount(document) });
            }
            using (var document = RhinoDoc.CreateHeadless(null))
            {
                document.UndoRecordingEnabled = true;
                Guid defaultId = document.Layers.CurrentLayer.Id;
                int index = document.Layers.Add(new Layer { Name = "SDK_DELETE_LAYER" });
                Guid target = document.Layers[index].Id;
                object result = Invoke(document, "DeleteLayer", "{\"guid\":\"" + target + "\"}");
                cases.Add(new { kind = "layer", result, targetId = target.ToString("D"),
                    targetMissing = !document.Layers.Any(layer => layer.Id == target && !layer.IsDeleted),
                    defaultPreserved = document.Layers.Any(layer => layer.Id == defaultId && !layer.IsDeleted),
                    currentLayerId = document.Layers.CurrentLayer.Id.ToString("D"),
                    defaultLayerId = defaultId.ToString("D") });
            }
            using (var document = RhinoDoc.CreateHeadless(null))
            {
                document.UndoRecordingEnabled = true;
                var type = bridge.GetType("RhinoMcpBridge.Functions.UndoGroups", true);
                var groups = Activator.CreateInstance(type, true);
                var owner = GroupContext(document, groups, "one");
                var other = GroupContext(document, groups, "two");
                var begin = InvokeCommand(owner, "UndoGroups", "Begin", "{\"name\":\"SDK group\"}");
                var prepare = type.GetMethod("PrepareCommand", BindingFlags.Instance | BindingFlags.NonPublic);
                var foreignWrite = Rejects(() => prepare.Invoke(groups, new object[] { other, "create_object", false }));
                var foreignEnd = Rejects(() => InvokeCommand(other, "UndoGroups", "End", "{}"));
                var undoInGroup = Rejects(() => prepare.Invoke(groups, new object[] { owner, "undo", false }));
                if (!(bool)prepare.Invoke(groups, new object[] { owner, "create_object", false }))
                    throw new InvalidOperationException("Group owner did not obtain the existing undo record.");
                document.Objects.AddPoint(new Point3d(1, 1, 1));
                prepare.Invoke(groups, new object[] { owner, "create_object", false });
                document.Objects.AddPoint(new Point3d(2, 2, 2));
                var end = InvokeCommand(owner, "UndoGroups", "End", "{}");
                var undo = InvokeCommand(owner, "UndoRedo", "Undo", "{}");
                var afterUndo = ActiveCount(document);
                var redo = InvokeCommand(owner, "UndoRedo", "Redo", "{}");
                var afterRedo = ActiveCount(document);
                InvokeCommand(owner, "UndoGroups", "Begin", "{\"name\":\"SDK forced\"}");
                var forced = InvokeCommand(other, "UndoGroups", "End", "{\"force\":true}");
                cases.Add(new { kind = "undo_group", begin, end, undo, redo, afterUndo, afterRedo,
                    foreignWrite, foreignEnd, undoInGroup, forced });
            }
            Console.WriteLine("RESULT_JSON=" + new JavaScriptSerializer().Serialize(new {
                softwareBuild = RhinoApp.Version.ToString(),
                bridgeVersion = bridge.GetName().Version.ToString(), cases }));
        }
        return 0;
    }

    static object GroupContext(RhinoDoc document, object groups, string owner)
    {
        var context = contextConstructor.Invoke(new object[] { document, null, null,
            .001, .017, document.ModelUnitSystem, "sdk-rhino-contract", CancellationToken.None });
        var flags = BindingFlags.Instance | BindingFlags.NonPublic;
        context.GetType().GetProperty("UndoGroupService", flags).SetValue(context, groups, null);
        context.GetType().GetProperty("CommandOwner", flags).SetValue(context, owner, null);
        return context;
    }

    static bool Rejects(Action invoke)
    {
        try { invoke(); return false; }
        catch (TargetInvocationException error)
        {
            var native = error.InnerException;
            var category = native.GetType().GetProperty("Category");
            return category != null && (string)category.GetValue(native, null) == "invalid_argument";
        }
    }

    static object Invoke(RhinoDoc document, string handler, string arguments)
    {
        var context = contextConstructor.Invoke(new object[] { document, null, null,
            .001, .017, document.ModelUnitSystem, "sdk-delete-contract", CancellationToken.None });
        return InvokeCommand(context, handler, "Delete", arguments);
    }

    static object InvokeCommand(object context, string handler, string method, string arguments)
    {
        Assembly json = Assembly.LoadFrom(Path.Combine(nativeRoot, "System.Text.Json.dll"));
        Type documentType = json.GetType("System.Text.Json.JsonDocument", true);
        Type optionsType = json.GetType("System.Text.Json.JsonDocumentOptions", true);
        object parsed = documentType.GetMethod("Parse", new[] { typeof(string), optionsType })
            .Invoke(null, new[] { (object)arguments, Activator.CreateInstance(optionsType) });
        try
        {
            object element = documentType.GetProperty("RootElement").GetValue(parsed, null);
            return bridge.GetType("RhinoMcpBridge.Functions." + handler, true)
                .GetMethod(method, BindingFlags.Static | BindingFlags.NonPublic)
                .Invoke(null, new[] { context, element });
        }
        finally { ((IDisposable)parsed).Dispose(); }
    }

    static bool Active(RhinoDoc document, Guid id)
    {
        var item = document.Objects.FindId(id);
        return item != null && !item.IsDeleted;
    }

    static int ActiveCount(RhinoDoc document)
    {
        return document.Objects.GetObjectList(ObjectType.AnyObject).Count(item => !item.IsDeleted);
    }
}
