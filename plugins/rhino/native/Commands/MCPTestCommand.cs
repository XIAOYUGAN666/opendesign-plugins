// Adapted from jingcheng-chen/rhinomcp, commit 3c192bd8c800360f9fbce70d921d9d94bc7dc77f.
using Rhino;
using Rhino.Commands;
using Rhino.Input;
using Rhino.Input.Custom;
using RhinoMcpBridge.Functions;

namespace RhinoMcpBridge.Commands
{
    public class MCPTestCommand : Command
    {
        public override string EnglishName => "mcptest";

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            // Parse options
            var visualOption = new OptionToggle(false, "Off", "On");
            var grasshopperOption = new OptionToggle(false, "Off", "On");
            var delayOption = new OptionInteger(500, 0, 5000);

            using var go = new GetOption();
            go.SetCommandPrompt("MCP Test options (press Enter to run with defaults)");
            go.AddOptionToggle("Visual", ref visualOption);
            go.AddOptionToggle("Grasshopper", ref grasshopperOption);
            go.AddOptionInteger("Delay", ref delayOption);
            go.AcceptNothing(true);

            while (true)
            {
                var result = go.Get();
                if (result == GetResult.Nothing || result == GetResult.Cancel)
                    break;
                if (result != GetResult.Option)
                    break;
            }

            return RunTests(doc, visualOption.CurrentValue, grasshopperOption.CurrentValue,
                delayOption.CurrentValue, line => RhinoApp.WriteLine(line));
        }

        internal static Result RunTests(RhinoDoc doc, bool visualMode, bool includeGrasshopper, int delayMs,
            Action<string> write)
        {
            if (visualMode)
            {
                write($"Running MCP function tests in VISUAL MODE (delay: {delayMs}ms)...");
            }
            else
            {
                write("Running MCP function tests...");
            }
            write("========================================");

            var functions = new NativeSelfTests(doc, write);
            var results = functions.TestAllFunctions(visualMode, delayMs);
            if (includeGrasshopper)
            {
                write("Running Grasshopper MCP function tests...");
                foreach (var prop in functions.TestGrasshopperFunctions(visualMode, delayMs).Properties())
                {
                    results[prop.Name] = prop.Value;
                }
            }

            // Print summary
            write("========================================");
            write("Test Summary:");

            int passed = 0;
            int failed = 0;

            foreach (var prop in results.Properties())
            {
                var status = prop.Value["status"]?.ToString();
                if (status == "pass")
                {
                    passed++;
                    var note = prop.Value["note"]?.ToString();
                    if (!string.IsNullOrEmpty(note))
                        write($"  [PASS] {prop.Name} ({note})");
                    else
                        write($"  [PASS] {prop.Name}");
                }
                else
                {
                    failed++;
                    var error = prop.Value["error"]?.ToString() ?? "Unknown error";
                    write($"  [FAIL] {prop.Name}: {error}");
                }
            }

            write("========================================");
            write($"Results: {passed} passed, {failed} failed, {passed + failed} total");

            if (failed == 0)
            {
                write("All tests passed!");
                return Result.Success;
            }
            else
            {
                write("Some tests failed. Check output above for details.");
                return Result.Failure;
            }
        }
    }
}
