#!/usr/bin/env python3
"""Regenerate the installer's componentValues typing in chart/values.schema.json.

For every component pinned in chart/values.yaml, pull its chart and embed that chart's
values.schema.json under componentValues.properties.<name>, so `componentValues.<name>`
is strictly typed against the component's REAL Composition schema (the component chart's
values are the Composition spec). `required` is stripped recursively because componentValues
is a PARTIAL override (a deep-merge), not a full values document.

Run this on every installer release — when a component's GVR/schema changes (a new component
version), re-running ties the installer's schema to the new composition schema. The installer
VERSION is the unit that manages the component GVRs (a new GVR -> a new installer version with
a regenerated values.schema.json).

Usage:  python3 hack/gen-componentvalues-schema.py [chart-dir]   (default: ./chart)
Requires: helm (logged in to the registry if components are private), pyyaml.
"""
import glob
import json
import os
import subprocess
import sys
import tempfile

import yaml

CHART = sys.argv[1] if len(sys.argv) > 1 else "chart"


def strip_required(node):
    """Recursively drop the JSON-Schema `required` keyword (partial overrides need none)."""
    if isinstance(node, dict):
        node.pop("required", None)
        for v in node.values():
            strip_required(v)
    elif isinstance(node, list):
        for v in node:
            strip_required(v)
    return node


def main():
    vals = yaml.safe_load(open(os.path.join(CHART, "values.yaml")))
    oci = vals["ociRepo"]
    props = {}
    with tempfile.TemporaryDirectory() as tmp:
        for c in vals["components"]:
            name, ver = c["name"], str(c["version"])
            ref = f'{c.get("repo", oci)}/{name}'
            dest = os.path.join(tmp, name)
            os.makedirs(dest, exist_ok=True)
            r = subprocess.run(
                ["helm", "pull", ref, "--version", ver, "-d", dest, "--untar"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                print(f"  WARN {name}: pull failed: {r.stderr.strip()[:90]}", file=sys.stderr)
                continue
            found = glob.glob(os.path.join(dest, "*", "values.schema.json"))
            if not found:
                print(f"  WARN {name}: no values.schema.json in chart", file=sys.stderr)
                continue
            s = json.load(open(found[0]))
            s.pop("$schema", None)
            s.pop("title", None)
            strip_required(s)
            s["description"] = f"Overrides for the {name} Composition (chart {ver}), deep-merged into its spec."
            props[name] = s
            print(f"  typed {name} ({ver})")

    sp = os.path.join(CHART, "values.schema.json")
    schema = json.load(open(sp))
    schema["properties"]["componentValues"] = {
        "type": "object",
        "title": "Per-component spec overrides",
        "description": (
            "Per-component Composition spec overrides, STRICTLY TYPED against each pinned "
            "component's chart schema (regenerated per installer version by "
            "hack/gen-componentvalues-schema.py). Deep-merged into the rendered Composition "
            "spec; the installer-computed wiring (service.type/config/vertexAI/hitlApproval) "
            "stays authoritative and wins on any leaf conflict."
        ),
        "additionalProperties": False,
        "properties": props,
    }
    json.dump(schema, open(sp, "w"), indent=2)
    open(sp, "a").write("\n")
    print(f"\nwrote componentValues typing for {len(props)} components -> {sp}")


if __name__ == "__main__":
    main()
