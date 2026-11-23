"""Upstream Grasshopper test observation variants."""

import copy


def variants(good):
    yield "correct", copy.deepcopy(good), True
    for name in [
        "solution_error",
        "missing_wire",
        "orphan",
        "wrong_count",
        "wrong_value",
        "truncated",
        "wrong_graph",
        "duplicate_nickname",
    ]:
        s = copy.deepcopy(good)
        first = next(iter(s["outputs"].values()))
        if name == "solution_error":
            s["solution"].update(success=False, error_count=1)
        elif name == "missing_wire":
            for node in s["canvas"]["components"]:
                for port in node["inputs"]:
                    if port.get("sources"):
                        port["sources"] = []
        elif name == "orphan":
            s["canvas"]["components"].append(
                {
                    "instance_id": "extra",
                    "name": "Series",
                    "nickname": "extra",
                    "inputs": [],
                    "outputs": [],
                }
            )
            s["graph"]["components"].append(s["canvas"]["components"][-1])
            s["canvas"]["object_count"] += 1
            s["document"]["object_count"] += 1
        elif name == "wrong_count":
            first["data_count"] += 1
        elif name == "wrong_value":
            first["values"][0]["items"][0] = "bad"
        elif name == "truncated":
            first["truncated"] = True
        elif name == "wrong_graph":
            s["graph"]["graph_id"] = "other"
        elif name == "duplicate_nickname":
            s["canvas"]["components"][0]["nickname"] = s["canvas"]["components"][-1][
                "nickname"
            ]
        yield name, s, False
