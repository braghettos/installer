#!/usr/bin/env python3
"""Regenerate componentValues.<component> in chart/values.schema.json so each
entry mirrors that component chart's OWN values.schema.json (strict, fully-typed).

Usage: python3 hack/gen-componentvalues-schema.py
"""
import json, os, subprocess, sys, tempfile, yaml

OCI = "oci://ghcr.io/braghettos/krateo"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(ROOT, "chart", "values.schema.json")
PINS = os.path.join(ROOT, "chart", "files", "component-pins.yaml")

schema = json.load(open(SCHEMA))
pins = yaml.safe_load(open(PINS))
comps = [(c["name"], c.get("chart") or c["name"], str(c["version"])) for c in pins["components"]]

cv = {}
with tempfile.TemporaryDirectory() as tmp:
    for name, chart, ver in comps:
        subprocess.run(["helm", "pull", f"{OCI}/{chart}", "--version", ver, "-d", tmp, "--untar"],
                       check=True, capture_output=True)
        sp = os.path.join(tmp, chart, "values.schema.json")
        if not os.path.exists(sp):
            sys.exit(f"ERROR: {chart}@{ver} ships no values.schema.json")
        cs = json.load(open(sp))
        cs.pop("$schema", None)   # inherit the installer root dialect (2020-12)
        cs.pop("$id", None)
        cv[name] = cs

# componentValues keeps additionalProperties:false -> only pinned components are accepted
schema["properties"]["componentValues"]["properties"] = dict(sorted(cv.items()))
json.dump(schema, open(SCHEMA, "w"), indent=2)
open(SCHEMA, "a").write("\n")
print(f"Embedded {len(cv)} component chart schemas into componentValues.")
