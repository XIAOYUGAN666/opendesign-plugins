// Adapted from jingcheng-chen/rhinomcp, commit 3c192bd8c800360f9fbce70d921d9d94bc7dc77f.
#nullable disable
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using Newtonsoft.Json.Linq;
using Rhino;

namespace RhinoMcpBridge.Functions;

internal sealed partial class NativeSelfTests
{
    /// <summary>
    /// Tests Grasshopper handler functions against the active Grasshopper canvas.
    /// This is intentionally separate from TestAllFunctions because it requires
    /// Grasshopper to be open with an active document.
    /// </summary>
    /// <param name="visualMode">If true, leaves test objects on the Grasshopper canvas for inspection</param>
    /// <param name="delayMs">Delay in milliseconds between tests in visual mode (0 = no delay)</param>
    /// <returns>JObject with test results for each Grasshopper handler group</returns>
    public JObject TestGrasshopperFunctions(bool visualMode = false, int delayMs = 500)
    {
        var results = new JObject();
        var createdIds = new List<string>();
        string sliderAId = null;
        string sliderBId = null;
        string addId = null;
        string panelId = null;
        int initialObjectCount = 0;

        void VisualUpdate(string testName)
        {
            if (!visualMode) return;

            _write($"  >> {testName}");
            RedrawGrasshopperCanvas(new System.Drawing.PointF(260, 90));
            if (delayMs <= 0)
            {
                RhinoApp.Wait();
                return;
            }

            var stopwatch = System.Diagnostics.Stopwatch.StartNew();
            while (stopwatch.ElapsedMilliseconds < delayMs)
            {
                RhinoApp.Wait();
                Thread.Sleep(10);
            }
        }

        bool HasTwoColumns(JToken layout) => layout is JArray entries &&
            entries.Select(entry => entry["position"]?[0]?.ToObject<double>()).Distinct().Count() == 2;

        void AddCreatedId(JObject result)
        {
            var id = result["instance_id"]?.ToString();
            if (!string.IsNullOrEmpty(id))
            {
                createdIds.Add(id);
            }
        }

        void Record(string name, Action test)
        {
            try
            {
                test();
                RecordPass(name);
            }
            catch (Exception e)
            {
                results[name] = new JObject { ["status"] = "fail", ["error"] = e.Message };
            }
        }

        void RecordPass(string name, string note = null)
        {
            var result = new JObject { ["status"] = "pass" };
            if (!string.IsNullOrEmpty(note))
            {
                result["note"] = note;
            }
            results[name] = result;
            VisualUpdate(name);
        }

        try
        {
            Record("gh_create_document", () =>
            {
                var created = Invoke("gh_create_document", new JObject
                {
                    ["new_if_missing"] = true,
                    ["make_active"] = true,
                    ["open_canvas"] = true
                });
                if (created["has_document"]?.Value<bool>() != true)
                {
                    throw new InvalidOperationException(created["message"]?.ToString() ?? "Failed to create or activate Grasshopper document.");
                }
                initialObjectCount = created["object_count"]?.Value<int>() ?? 0;
            });

            if (results["gh_create_document"]?["status"]?.ToString() != "pass")
            {
                return results;
            }

            Record("gh_get_document_info", () =>
            {
                var info = Invoke("gh_get_document_info", new JObject());
                if (info["has_document"]?.Value<bool>() != true)
                {
                    throw new InvalidOperationException(
                        "No active Grasshopper document. Open Grasshopper and create/open a document before running mcptest Grasshopper=On.");
                }
            });

            if (results["gh_get_document_info"]?["status"]?.ToString() != "pass")
            {
                return results;
            }

            Record("gh_search_components", () =>
            {
                var search = Invoke("gh_search_components", new JObject
                {
                    ["query"] = "Addition",
                    ["limit"] = 5
                });
                if ((search["count"]?.ToObject<int>() ?? 0) < 1)
                {
                    throw new InvalidOperationException("Could not find the Grasshopper Addition component.");
                }
            });

            Record("gh_list_component_categories", () =>
            {
                var categories = Invoke("gh_list_component_categories", new JObject());
                if ((categories["category_count"]?.ToObject<int>() ?? 0) < 1)
                {
                    throw new InvalidOperationException("No Grasshopper component categories were returned.");
                }
            });

            Record("gh_get_available_components", () =>
            {
                var available = Invoke("gh_get_available_components", new JObject { ["limit"] = 10 });
                if ((available["count"]?.ToObject<int>() ?? 0) < 1)
                {
                    throw new InvalidOperationException("No available Grasshopper components were returned.");
                }
            });

            Record("gh_get_component_type_info", () =>
            {
                var typeInfo = Invoke("gh_get_component_type_info", new JObject { ["name"] = "Addition" });
                if ((typeInfo["inputs"] as JArray)?.Count < 2 || typeInfo["guid"]?.ToString() is not { Length: > 0 })
                {
                    throw new InvalidOperationException("Addition component did not expose the expected inputs.");
                }
            });

            Record("gh_batch_get_component_type_info", () =>
            {
                var batchInfo = Invoke("gh_batch_get_component_type_info", new JObject
                {
                    ["components"] = new JArray
                    {
                        new JObject { ["name"] = "Number Slider" },
                        new JObject { ["name"] = "Addition" },
                        new JObject { ["name"] = "Panel" }
                    }
                });
                if ((batchInfo["found_count"]?.ToObject<int>() ?? 0) != 3)
                {
                    throw new InvalidOperationException("Batch type info did not resolve the expected components.");
                }
            });

            Record("gh_batch_search_components", () =>
            {
                var batch = Invoke("gh_batch_search_components", new JObject
                {
                    ["queries"] = new JArray { "Number Slider", "Addition", "Panel" }
                });
                if ((batch["results"] as JObject)?.Properties().Count(item => item.Value is JObject) != 3)
                {
                    throw new InvalidOperationException("Batch search did not resolve the expected Grasshopper components.");
                }
            });

            Record("gh_build_graph", () =>
            {
                var graph = Invoke("gh_build_graph", new JObject
                {
                    ["graph_id"] = "MCPTestMutation",
                    ["components"] = new JArray
                    {
                        new JObject
                        {
                            ["alias"] = "bg_a",
                            ["component_name"] = "Number Slider",
                            ["nickname"] = "BG_A",
                            ["value"] = 1.25,
                            ["min"] = 0,
                            ["max"] = 10,
                            ["decimals"] = 2
                        },
                        new JObject
                        {
                            ["alias"] = "bg_b",
                            ["component_name"] = "Number Slider",
                            ["nickname"] = "BG_B",
                            ["value"] = 2.75,
                            ["min"] = 0,
                            ["max"] = 10,
                            ["decimals"] = 2
                        },
                        new JObject
                        {
                            ["alias"] = "bg_add",
                            ["component_name"] = "Addition",
                            ["nickname"] = "BG_Add"
                        },
                        new JObject
                        {
                            ["alias"] = "bg_panel",
                            ["component_name"] = "Panel",
                            ["nickname"] = "BG_Out"
                        }
                    },
                    ["connections"] = new JArray
                    {
                        new JObject { ["source"] = "bg_a", ["target"] = "bg_add", ["target_input_index"] = 0 },
                        new JObject { ["source"] = "bg_b", ["target"] = "bg_add", ["target_input_index"] = 1 },
                        new JObject { ["source"] = "bg_add", ["source_output_index"] = 0, ["target"] = "bg_panel" }
                    },
                    ["layout"] = new JObject
                    {
                        ["enabled"] = true,
                        ["start_position"] = new JArray { 40, 260 },
                        ["x_spacing"] = 220,
                        ["y_spacing"] = 90,
                        ["max_columns"] = 2
                    },
                    ["recompute"] = true,
                    ["rollback_on_error"] = true
                });

                if ((graph["component_count"]?.ToObject<int>() ?? 0) != 4)
                {
                    throw new InvalidOperationException("gh_build_graph did not create the expected components.");
                }
                if ((graph["connection_count"]?.ToObject<int>() ?? 0) != 3)
                {
                    throw new InvalidOperationException("gh_build_graph did not create the expected connections.");
                }
                if ((graph["layout"] as JArray)?.Count != 4)
                {
                    throw new InvalidOperationException("gh_build_graph did not lay out the expected components.");
                }
                if (!HasTwoColumns(graph["layout"]))
                {
                    throw new InvalidOperationException("gh_build_graph did not apply wrapped layout max_columns.");
                }
                if ((graph["summary"]?["object_count"]?.ToObject<int>() ?? 0) < 4)
                {
                    throw new InvalidOperationException("gh_build_graph did not return diagnostics for created components.");
                }

                var aliases = graph["aliases"] as JObject;
                foreach (string alias in new[] { "bg_a", "bg_b", "bg_add", "bg_panel" })
                {
                    var id = aliases?[alias]?.ToString();
                    if (string.IsNullOrEmpty(id))
                    {
                        throw new InvalidOperationException($"gh_build_graph did not return alias '{alias}'.");
                    }
                    createdIds.Add(id);
                }
            });

            Record("gh_mutate_graph", () =>
            {
                var mutation = Invoke("gh_mutate_graph", new JObject
                {
                    ["graph_id"] = "MCPTestMutation",
                    ["operations"] = new JArray
                    {
                        new JObject
                        {
                            ["op"] = "create",
                            ["alias"] = "mut_panel",
                            ["component_name"] = "Panel",
                            ["nickname"] = "Mut_Out",
                            ["role"] = "output"
                        },
                        new JObject
                        {
                            ["op"] = "set",
                            ["target"] = "bg_a",
                            ["value"] = 2.0,
                            ["min"] = 0,
                            ["max"] = 10,
                            ["decimals"] = 2
                        },
                        new JObject
                        {
                            ["op"] = "connect",
                            ["source"] = "bg_add",
                            ["source_output_index"] = 0,
                            ["target"] = "mut_panel"
                        },
                        new JObject
                        {
                            ["op"] = "update",
                            ["target"] = "bg_b",
                            ["preview"] = false
                        },
                        new JObject { ["op"] = "recompute" }
                    },
                    ["preview_policy"] = new JObject
                    {
                        ["mode"] = "only",
                        ["targets"] = new JArray { "mut_panel" },
                        ["scope"] = new JArray { "bg_a", "bg_b", "bg_add", "mut_panel" }
                    },
                    ["groups"] = new JArray
                    {
                        new JObject
                        {
                            ["name"] = "Mutation Output",
                            ["targets"] = new JArray { "bg_a", "bg_b", "bg_add", "mut_panel" },
                            ["color"] = new JArray { 180, 220, 255 }
                        }
                    },
                    ["layout"] = new JObject
                    {
                        ["enabled"] = true,
                        ["targets"] = new JArray { "bg_a", "bg_b", "bg_add", "mut_panel" },
                        ["start_position"] = new JArray { 40, 430 },
                        ["max_columns"] = 2
                    },
                    ["verify"] = new JObject
                    {
                        ["run_solution"] = true,
                        ["outputs"] = new JArray
                        {
                            new JObject
                            {
                                ["target"] = "bg_add",
                                ["output_index"] = 0,
                                ["expect_count_min"] = 1,
                                ["expect_type"] = "Number"
                            }
                        }
                    },
                    ["recompute"] = true,
                    ["rollback_on_error"] = true
                });

                if ((mutation["operation_count"]?.ToObject<int>() ?? 0) != 5)
                {
                    throw new InvalidOperationException("gh_mutate_graph did not report success.");
                }
                if (mutation["verification"]?["passed"]?.ToObject<bool>() != true)
                {
                    throw new InvalidOperationException($"gh_mutate_graph verification failed: {mutation["verification"]}");
                }
                if (!HasTwoColumns(mutation["layout"]))
                {
                    throw new InvalidOperationException("gh_mutate_graph did not apply wrapped layout max_columns.");
                }
                if ((mutation["summary"]?["object_count"]?.ToObject<int>() ?? 0) < 1)
                {
                    throw new InvalidOperationException("gh_mutate_graph did not return diagnostics.");
                }

                var operations = mutation["operations"] as JArray;
                foreach (var op in operations?.OfType<JObject>() ?? Enumerable.Empty<JObject>())
                {
                    if (op["op"]?.ToString() == "create")
                    {
                        var id = op["object"]?["instance_id"]?.ToString();
                        if (!string.IsNullOrEmpty(id))
                        {
                            createdIds.Add(id);
                        }
                    }
                }
                foreach (var group in (mutation["groups"] as JArray)?.OfType<JObject>() ?? Enumerable.Empty<JObject>())
                {
                    var id = group["instance_id"]?.ToString();
                    if (!string.IsNullOrEmpty(id))
                    {
                        createdIds.Add(id);
                    }
                }
            });

            Record("gh_capture_preview", () =>
            {
                var previewGraph = Invoke("gh_build_graph", new JObject
                {
                    ["graph_id"] = "MCPTestPreview",
                    ["components"] = new JArray
                    {
                        new JObject
                        {
                            ["alias"] = "preview_point",
                            ["component_name"] = "Point",
                            ["nickname"] = "PreviewPt",
                            ["role"] = "output"
                        }
                    },
                    ["values"] = new JArray
                    {
                        new JObject
                        {
                            ["target"] = "preview_point",
                            ["value"] = new JArray { 0, 0, 0 }
                        }
                    },
                    ["recompute"] = true,
                    ["rollback_on_error"] = true
                });

                var previewAliases = previewGraph["aliases"] as JObject;
                var previewPointId = previewAliases?["preview_point"]?.ToString();
                if (!string.IsNullOrEmpty(previewPointId))
                {
                    createdIds.Add(previewPointId);
                }

                var preview = Invoke("gh_capture_preview", new JObject
                {
                    ["graph_id"] = "MCPTestPreview",
                    ["viewport"] = "top",
                    ["width"] = 160,
                    ["height"] = 120,
                    ["show_grid"] = false,
                    ["show_axes"] = false,
                    ["recompute"] = true
                });
                if (string.IsNullOrWhiteSpace(preview["image_data"]?.ToString()))
                {
                    throw new InvalidOperationException("gh_capture_preview did not return image data.");
                }
                if ((preview["captured_preview_object_count"]?.ToObject<int>() ?? 0) < 1)
                {
                    throw new InvalidOperationException("gh_capture_preview did not capture any preview-capable objects.");
                }
            });

            Record("gh_mutate_graph_fail_on_verification_error", () =>
            {
                bool threw = false;
                try
                {
                    Invoke("gh_mutate_graph", new JObject
                    {
                        ["graph_id"] = "MCPTestMutation",
                        ["operations"] = new JArray { new JObject { ["op"] = "recompute" } },
                        ["verify"] = new JObject
                        {
                            ["run_solution"] = true,
                            ["outputs"] = new JArray
                            {
                                new JObject
                                {
                                    ["target"] = "bg_add",
                                    ["output_index"] = 0,
                                    ["expect_count_exact"] = 999
                                }
                            }
                        },
                        ["fail_on_verification_error"] = true,
                        ["recompute"] = true,
                        ["rollback_on_error"] = true
                    });
                }
                catch (Exception e)
                {
                    threw = true;
                    if (e is not NativeCommandException { Category: "graphVerificationFailed" })
                    {
                        throw new InvalidOperationException($"Unexpected verification failure message: {e.Message}");
                    }
                }

                if (!threw)
                {
                    throw new InvalidOperationException("gh_mutate_graph did not fail when verification failed with fail_on_verification_error=true.");
                }
            });

            Record("gh_get_graph", () =>
            {
                var graph = Invoke("gh_get_graph", new JObject
                {
                    ["graph_id"] = "MCPTestMutation",
                    ["include_values"] = true,
                    ["max_items"] = 5
                });
                if ((graph["object_count"]?.ToObject<int>() ?? 0) < 2)
                {
                    throw new InvalidOperationException("gh_get_graph did not return the expected mutation graph objects.");
                }
            });

            Record("gh_clear_graph", () =>
            {
                var cleared = Invoke("gh_clear_graph", new JObject
                {
                    ["graph_id"] = "MCPTestMutation",
                    ["include_groups"] = true,
                    ["recompute"] = false
                });
                if ((cleared["deleted_count"]?.ToObject<int>() ?? 0) < 1)
                {
                    throw new InvalidOperationException("gh_clear_graph did not delete the expected mutation graph objects.");
                }

                var remaining = Invoke("gh_get_graph", new JObject { ["graph_id"] = "MCPTestMutation" });
                if ((remaining["object_count"]?.ToObject<int>() ?? 0) != 0)
                {
                    throw new InvalidOperationException("gh_clear_graph left tagged mutation graph objects behind.");
                }

                var idsToRemove = createdIds
                    .Where(id =>
                    {
                        try
                        {
                            return GetActiveGrasshopperDocument().FindObject(Guid.Parse(id), true) == null;
                        }
                        catch
                        {
                            return false;
                        }
                    })
                    .ToList();
                foreach (var id in idsToRemove)
                {
                    createdIds.Remove(id);
                }
            });

            Record("gh_add_component", () =>
            {
                var sliderA = Invoke("gh_add_component", new JObject
                {
                    ["component_name"] = "Number Slider",
                    ["nickname"] = "A",
                    ["position"] = new JArray { 40, 40 },
                    ["value"] = 3.5,
                    ["min"] = 0,
                    ["max"] = 10,
                    ["decimals"] = 2
                });
                AddCreatedId(sliderA);
                sliderAId = sliderA["instance_id"]?.ToString();

                var sliderB = Invoke("gh_add_component", new JObject
                {
                    ["component_name"] = "Number Slider",
                    ["nickname"] = "B",
                    ["position"] = new JArray { 40, 140 },
                    ["value"] = 4.5,
                    ["min"] = 0,
                    ["max"] = 10,
                    ["decimals"] = 2
                });
                AddCreatedId(sliderB);
                sliderBId = sliderB["instance_id"]?.ToString();

                var addition = Invoke("gh_add_component", new JObject
                {
                    ["component_name"] = "Addition",
                    ["nickname"] = "Add",
                    ["position"] = new JArray { 240, 80 }
                });
                AddCreatedId(addition);
                addId = addition["instance_id"]?.ToString();

                var panel = Invoke("gh_add_component", new JObject
                {
                    ["component_name"] = "Panel",
                    ["nickname"] = "Out",
                    ["position"] = new JArray { 420, 40 },
                    ["content"] = "MCP Grasshopper test"
                });
                AddCreatedId(panel);
                panelId = panel["instance_id"]?.ToString();

                if (string.IsNullOrEmpty(sliderAId) ||
                    string.IsNullOrEmpty(sliderBId) ||
                    string.IsNullOrEmpty(addId) ||
                    string.IsNullOrEmpty(panelId))
                {
                    throw new InvalidOperationException("One or more Grasshopper test components did not return an instance_id.");
                }
            });

            Record("gh_list_components", () =>
            {
                var components = Invoke("gh_list_components", new JObject
                {
                    ["name"] = "Add",
                    ["limit"] = 10
                });
                if ((components["count"]?.ToObject<int>() ?? 0) < 1)
                {
                    throw new InvalidOperationException("Added Grasshopper Addition component was not listed.");
                }
            });

            Record("gh_get_component_info", () =>
            {
                var info = Invoke("gh_get_component_info", new JObject { ["instance_id"] = addId });
                if (info["instance_id"]?.ToString() != addId)
                {
                    throw new InvalidOperationException("Component info did not return the expected Addition instance.");
                }
            });

            Record("gh_set_parameter_value", () =>
            {
                var sliderASet = Invoke("gh_set_parameter_value", new JObject
                {
                    ["instance_id"] = sliderAId,
                    ["value"] = 3.5,
                    ["min"] = 0,
                    ["max"] = 10,
                    ["decimals"] = 2
                });
                if (Math.Abs((sliderASet["value"]?.ToObject<double>() ?? 0) - 3.5) > 0.001)
                {
                    throw new InvalidOperationException("First slider value was not set to 3.5.");
                }

                var sliderBSet = Invoke("gh_set_parameter_value", new JObject
                {
                    ["instance_id"] = sliderBId,
                    ["value"] = 4.5,
                    ["min"] = 0,
                    ["max"] = 10,
                    ["decimals"] = 2
                });
                if (Math.Abs((sliderBSet["value"]?.ToObject<double>() ?? 0) - 4.5) > 0.001)
                {
                    throw new InvalidOperationException("Second slider value was not set to 4.5.");
                }
            });

            Record("gh_connect_components", () =>
            {
                var first = Invoke("gh_connect_components", new JObject
                {
                    ["source_instance_id"] = sliderAId,
                    ["target_instance_id"] = addId,
                    ["target_input_index"] = 0
                });
                if (first["target_id"]?.ToString() != addId)
                {
                    throw new InvalidOperationException("First slider was not connected to the Addition input.");
                }

                var second = Invoke("gh_connect_components", new JObject
                {
                    ["source_instance_id"] = sliderBId,
                    ["target_instance_id"] = addId,
                    ["target_input_index"] = 1
                });
                if (second["target_id"]?.ToString() != addId)
                {
                    throw new InvalidOperationException("Second slider was not connected to the Addition input.");
                }

                var third = Invoke("gh_connect_components", new JObject
                {
                    ["source_instance_id"] = addId,
                    ["source_output_index"] = 0,
                    ["target_instance_id"] = panelId
                });
                if (third["target_id"]?.ToString() != panelId)
                {
                    throw new InvalidOperationException("Addition output was not connected to the Panel.");
                }
            });

            Record("gh_layout_components", () =>
            {
                var layout = Invoke("gh_layout_components", new JObject
                {
                    ["component_ids"] = new JArray { sliderAId, sliderBId, addId, panelId },
                    ["start_position"] = new JArray { 40, 40 },
                    ["x_spacing"] = 220,
                    ["y_spacing"] = 90,
                    ["recompute"] = true
                });
                if ((layout["layout_count"]?.ToObject<int>() ?? 0) != 4
                    || (layout["positions"] as JArray)?.Count != 4)
                {
                    throw new InvalidOperationException("Grasshopper layout did not position the expected test objects.");
                }
            });

            Record("gh_run_solution", () =>
            {
                var solution = Invoke("gh_run_solution", new JObject { ["expire_all"] = true });
                if ((solution["error_count"]?.ToObject<int>() ?? -1) != 0)
                {
                    throw new InvalidOperationException(solution["message"]?.ToString() ?? "Grasshopper solution failed.");
                }
            });

            Record("gh_get_parameter_value", () =>
            {
                JObject ReadAdditionValue()
                {
                    return Invoke("gh_get_parameter_value", new JObject
                    {
                        ["instance_id"] = addId,
                        ["output_index"] = 0,
                        ["max_items"] = 5
                    });
                }

                double? FirstValue(JObject value)
                {
                    var values = value["values"] as JArray;
                    var items = values != null && values.Count > 0 ? values[0]?["items"] as JArray : null;
                    return items != null && items.Count > 0 ? items[0]?.ToObject<double>() : null;
                }

                var value = ReadAdditionValue();
                var firstValue = FirstValue(value);
                if (!firstValue.HasValue)
                {
                    Invoke("gh_expire_solution", new JObject
                    {
                        ["component_ids"] = new JArray { addId },
                        ["expire_downstream"] = true,
                        ["recompute"] = true
                    });
                    Invoke("gh_run_solution", new JObject { ["expire_all"] = true });
                    RhinoApp.Wait();
                    value = ReadAdditionValue();
                    firstValue = FirstValue(value);
                }

                if (!firstValue.HasValue || Math.Abs(firstValue.Value - 8.0) > 0.001)
                {
                    var info = Invoke("gh_get_component_info", new JObject { ["instance_id"] = addId });
                    throw new InvalidOperationException(
                        $"Expected Addition output 8.0, got {firstValue?.ToString() ?? "no value"}. " +
                        $"Raw value: {value.ToString(Newtonsoft.Json.Formatting.None)}. " +
                        $"Component info: {info.ToString(Newtonsoft.Json.Formatting.None)}.");
                }
            });

            Record("gh_get_canvas_state", () =>
            {
                var state = Invoke("gh_get_canvas_state", new JObject
                {
                    ["include_connections"] = true,
                    ["include_values"] = true,
                    ["max_items"] = 5
                });
                if ((state["object_count"]?.ToObject<int>() ?? 0) < createdIds.Count)
                {
                    throw new InvalidOperationException("Canvas state did not include the created Grasshopper test objects.");
                }
            });

            Record("gh_update_component", () =>
            {
                var updated = Invoke("gh_update_component", new JObject
                {
                    ["instance_id"] = addId,
                    ["new_nickname"] = "Sum",
                    ["position"] = new JArray { 240, 80 },
                    ["preview"] = false
                });
                if (updated["nickname"]?.ToString() != "Sum")
                {
                    throw new InvalidOperationException("Component nickname was not updated.");
                }
            });

            Record("gh_expire_solution", () =>
            {
                var expired = Invoke("gh_expire_solution", new JObject
                {
                    ["component_ids"] = new JArray { addId },
                    ["recompute"] = true
                });
                if ((expired["expired_count"]?.ToObject<int>() ?? 0) < 1)
                {
                    throw new InvalidOperationException("No Grasshopper objects were expired.");
                }
            });

            Record("gh_disconnect_components", () =>
            {
                var disconnected = Invoke("gh_disconnect_components", new JObject
                {
                    ["target_instance_id"] = addId,
                    ["target_input_index"] = 0,
                    ["disconnect_all"] = true
                });
                if ((disconnected["disconnected_count"]?.ToObject<int>() ?? 0) < 1)
                {
                    throw new InvalidOperationException("No Grasshopper connections were disconnected.");
                }
            });

            Record("gh_delete_component", () =>
            {
                var deleteTarget = Invoke("gh_add_component", new JObject
                {
                    ["component_name"] = "Panel",
                    ["nickname"] = "Trash",
                    ["position"] = new JArray { 620, 40 },
                    ["content"] = "Delete target"
                });
                AddCreatedId(deleteTarget);
                var deleteTargetId = deleteTarget["instance_id"]?.ToString();
                if (string.IsNullOrEmpty(deleteTargetId))
                {
                    throw new InvalidOperationException("Delete test target did not return an instance_id.");
                }

                var deleted = Invoke("gh_delete_component", new JObject { ["instance_id"] = deleteTargetId });
                createdIds.Remove(deleteTargetId);
                if (deleted["deleted_id"]?.ToString() != deleteTargetId)
                {
                    throw new InvalidOperationException("Grasshopper delete did not return the expected deleted_id.");
                }
            });

            if (visualMode)
            {
                RecordPass("gh_clear_canvas", "Skipped in visual mode so the Grasshopper test graph remains inspectable.");
            }
            else if (initialObjectCount == 0)
            {
                Record("gh_clear_canvas", () =>
                {
                    var expectedMinimum = createdIds.Count;
                    var cleared = Invoke("gh_clear_canvas", new JObject
                    {
                        ["include_groups"] = true,
                        ["recompute"] = false
                    });
                    if ((cleared["deleted_count"]?.ToObject<int>() ?? 0) < expectedMinimum)
                    {
                        throw new InvalidOperationException("Grasshopper clear canvas did not remove the expected test objects.");
                    }
                    createdIds.Clear();
                });
            }
            else
            {
                RecordPass("gh_clear_canvas", "Skipped because the active Grasshopper canvas was not empty before the test.");
            }
        }
        finally
        {
            if (!visualMode && createdIds.Count > 0)
            {
                foreach (var id in createdIds)
                {
                    try
                    {
                        Invoke("gh_delete_component", new JObject { ["instance_id"] = id });
                    }
                    catch
                    {
                        // Ignore cleanup errors so the original test failure is preserved.
                    }
                }
            }
            else if (visualMode)
            {
                _write("Visual mode: Grasshopper test objects left on canvas for inspection");
                RedrawGrasshopperCanvas(new System.Drawing.PointF(260, 90));
                RhinoApp.Wait();
            }
        }

        return results;
    }
}
