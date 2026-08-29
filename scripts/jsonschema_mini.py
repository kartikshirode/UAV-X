#!/usr/bin/env python3
"""The slice of JSON Schema this repo actually uses, with no dependency.

`pip install jsonschema` into the gate's Python is one more thing that can be
absent on the machine that matters, and this repo has already lost time to a
check that passed because the thing doing the checking was not installed. Two
schemas need validating, `scenarios/run-record.schema.json` and
`submission/human-preflight.schema.json`, and they use eight keywords between
them.

Supported: type, required, properties, additionalProperties (as a schema),
items, enum, const, pattern, minLength, minimum, minItems, format (date and
date-time only), and allOf with if/then/else. Anything else in a schema is
ignored rather than silently trusted, so if you add a keyword, add it here too.

Round 6 finding 5: the three evidence blocks a scenario has to carry are only
required for the scenarios that produce them, and this file quietly ignored
`if` and `then`, so the schema said one thing and the validator enforced
nothing. A conditional that is ignored is worse than one that is absent,
because the schema reads as though it is being checked.

    from jsonschema_mini import validate
    errors = validate(instance, schema)
"""

import re
from datetime import date, datetime

TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value, want) -> bool:
    if isinstance(want, list):
        return any(_type_ok(value, w) for w in want)
    py = TYPES.get(want)
    if py is None:
        return True
    if want == "integer" and isinstance(value, bool):
        return False
    if want in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, py)


def validate(instance, schema, path: str = "") -> list:
    """Return a list of human-readable problems. Empty means valid."""
    errs: list[str] = []
    where = path or "(root)"

    if "type" in schema and not _type_ok(instance, schema["type"]):
        return [f"{where} should be {schema['type']}, got "
                f"{type(instance).__name__}"]

    if "enum" in schema and instance not in schema["enum"]:
        errs.append(f"{where} is {instance!r}, not one of {schema['enum']}")

    if "const" in schema and instance != schema["const"]:
        errs.append(f"{where} is {instance!r}, not {schema['const']!r}")

    if isinstance(instance, str):
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errs.append(f"{where} does not match {schema['pattern']}")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errs.append(f"{where} is shorter than {schema['minLength']}")
        fmt = schema.get("format")
        if fmt == "date":
            try:
                date.fromisoformat(instance)
            except ValueError:
                errs.append(f"{where} is not a YYYY-MM-DD date: {instance!r}")
        elif fmt == "date-time":
            try:
                datetime.fromisoformat(instance.replace("Z", "+00:00"))
            except ValueError:
                errs.append(f"{where} is not a timestamp: {instance!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errs.append(f"{where} is {instance}, below the minimum "
                        f"{schema['minimum']}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errs.append(f"{where} has {len(instance)} items, needs at least "
                        f"{schema['minItems']}")
        sub = schema.get("items")
        if isinstance(sub, dict):
            for i, item in enumerate(instance):
                errs += validate(item, sub, f"{where}[{i}]")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errs.append(f"{where} is missing {key!r}")
        props = schema.get("properties", {})
        for key, value in instance.items():
            if key in props:
                errs += validate(value, props[key], f"{where}.{key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errs += validate(value, schema["additionalProperties"],
                                 f"{where}.{key}")

    for i, sub in enumerate(schema.get("allOf", [])):
        errs += validate(instance, sub, path)

    # if/then/else, per the JSON Schema conditionals reference. The `if` is a
    # test and never a source of errors: failing it selects the `else` branch,
    # which is usually absent, and that is a pass.
    if "if" in schema:
        matched = not validate(instance, schema["if"], path)
        branch = schema.get("then") if matched else schema.get("else")
        if isinstance(branch, dict):
            errs += validate(instance, branch, path)

    return errs
