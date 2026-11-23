"""Upstream quarter-strip evaluator and sampling grid."""

import math

GRID = [-20, 10, 30, 50, 70, 95, 105, 125]
EXPECTED_MEMBERSHIP = [
    0 < z < 10 and x > 0 and y > 0 and 90**2 < x * x + y * y < 110**2
    for z in [-2, 5, 12]
    for x in GRID
    for y in GRID
]
EXPECTED_PASS = {
    "independent_correct",
    "independent_outward",
    "supervisor_capped_control",
}
CASES = {
    "mcp_closed_false",
    "mcp_closed_true",
    "supervisor_capped_control",
    "independent_correct",
    "independent_outward",
    "wrong_width",
    "equal_volume_wrong_radius",
    "wrong_position",
    "extra_object",
}


def evaluate(measured):
    objects = measured["objects"]
    one = objects[0] if len(objects) == 1 else {}

    def close(value, target):
        return (
            isinstance(value, (int, float))
            and math.isfinite(value)
            and abs(value - target) <= max(0.01, abs(target) * 1e-5)
        )

    checks = {
        "one_object": len(objects) == 1,
        "millimeters": measured["units"] == "Millimeters",
        "valid": one.get("valid") is True,
        "closed_solid": one.get("solid") is True,
        "no_naked_edges": one.get("naked_edges") == 0,
        "volume": close(one.get("volume"), 10000 * math.pi),
        "area": close(one.get("area"), 3000 * math.pi + 400),
        "bounds": all(
            close(a, b)
            for a, b in zip(
                one.get("min", []) + one.get("max", []), [0, 0, 0, 110, 110, 10]
            )
        )
        and len(one.get("min", []) + one.get("max", [])) == 6,
        "membership": one.get("membership") == EXPECTED_MEMBERSHIP,
    }
    return {
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "measurements": measured,
    }
