// Test-only probe: actual compiled Bridge and detached SDK component metadata.
// No user file, canvas, solution, plug-in entry point, or Host lease is used.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Web.Script.Serialization;
using Rhino;
using Rhino.Runtime.InProcess;

internal static class NativeGhMetadataSdkProbe
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
        var cases = new List<object>();
        using (var core = new RhinoCore(new[] { "/NOSPLASH" }, WindowStyle.NoWindow))
        {
            var gh = Assembly.LoadFrom(Path.Combine(sdkRoot,"..","Plug-ins","Grasshopper","Grasshopper.dll"));
            var server = gh.GetType("Grasshopper.Instances",true).GetProperty("ComponentServer").GetValue(null,null);
            var property = server.GetType().GetProperty("ObjectProxies");
            var proxies = ((System.Collections.IEnumerable)property.GetValue(server,null)).Cast<object>().ToArray();
            if (RhinoApp.Version.ToString() != "8.26.25349.19001") throw new InvalidOperationException("Exact accepted Rhino build required.");
            var helpers = bridge.GetType("RhinoMcpBridge.Functions.GrasshopperHelpers",true);
            var requireProxy = helpers.GetMethod("RequireProxy", BindingFlags.Static | BindingFlags.NonPublic);
            var addition = requireProxy.Invoke(null, new object[] { "Addition", null });
            var addAlias = requireProxy.Invoke(null, new object[] { "Add", null });
            var selectedId = (Guid)addition.GetType().GetProperty("Guid").GetValue(addition, null);
            var aliasId = (Guid)addAlias.GetType().GetProperty("Guid").GetValue(addAlias, null);
            var search = (System.Collections.IEnumerable)helpers.GetMethod("SearchProxies", BindingFlags.Static | BindingFlags.NonPublic)
                .Invoke(null, new object[] { "Addition", null, 10 });
            var first = search.Cast<object>().First();
            var firstId = (Guid)first.GetType().GetProperty("Guid").GetValue(first, null);
            if (selectedId != aliasId || selectedId != firstId)
                throw new InvalidOperationException("Component resolution, alias, and search chose different Addition components.");
            if ((bool)addition.GetType().GetProperty("Obsolete").GetValue(addition, null))
                throw new InvalidOperationException("Search chose an obsolete Addition component.");
            foreach(var id in new[]{"a0d62394-a118-422d-abb3-6af115c75b25","57da07bd-ecab-415d-9d86-af36d7073abc","59e0b89a-e487-49f8-bab8-b5bab16be14c"})
            {
                var proxy=proxies.SingleOrDefault(p=>(Guid)p.GetType().GetProperty("Guid").GetValue(p,null)==new Guid(id));
                if(proxy==null){cases.Add(new{id,error="proxyNotLoaded"});continue;}
                try{
                    var result=helpers.GetMethod("ComponentTypeInfo",BindingFlags.Static|BindingFlags.NonPublic).Invoke(null,new object[]{proxy,true});
                    var wire=bridge.GetType("RhinoMcpBridge.BridgeWire",true);
                    var bytes=(byte[])wire.GetMethod("Serialize",BindingFlags.Static|BindingFlags.NonPublic|BindingFlags.Public).Invoke(null,new[]{result});
                    cases.Add(new{id,json=System.Text.Encoding.UTF8.GetString(bytes)});
                }catch(Exception error){
                    while(error is TargetInvocationException && error.InnerException!=null)error=error.InnerException;
                    cases.Add(new{id,errorType=error.GetType().FullName,error=error.Message,stack=error.StackTrace});
                }
            }
            Console.WriteLine("RESULT_JSON="+new JavaScriptSerializer().Serialize(new{softwareBuild=RhinoApp.Version.ToString(),cases}));
        }
        return 0;
    }

}
