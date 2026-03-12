#!/usr/bin/env python3
"""Repair nightshift.org: remove duplicate properties, fix orphaned blocks."""
import re
from pathlib import Path

fp = Path.home() / "Data/0-personal/org/nightshift.org"
content = fp.read_text()
lines = content.split("\n")

output = []
i = 0
dup_fixes = 0
orphan_fixes = 0

while i < len(lines):
    line = lines[i]

    # Detect orphaned property lines outside :PROPERTIES: blocks
    if re.match(r'^:(NIGHTSHIFT_\w+):', line.strip()):
        in_props = False
        for prev in reversed(output[-20:]):
            s = prev.strip()
            if s == ":END:":
                break
            if s == ":PROPERTIES:":
                in_props = True
                break
        if not in_props:
            orphan_fixes += 1
            i += 1
            continue

    # Detect orphaned :END: outside property blocks
    if line.strip() == ":END:":
        in_props = False
        for prev in reversed(output[-20:]):
            s = prev.strip()
            if s == ":END:":
                break
            if s == ":PROPERTIES:":
                in_props = True
                break
        if not in_props:
            orphan_fixes += 1
            i += 1
            continue

    if line.strip() == ":PROPERTIES:":
        # Collect entire property block
        props = {}  # key -> (value, original_line)
        prop_order = []
        j = i + 1
        while j < len(lines) and lines[j].strip() != ":END:":
            prop_line = lines[j]
            m = re.match(r'^(\s*):(\w+):\s*(.*)', prop_line)
            if m:
                indent, key, val = m.group(1), m.group(2), m.group(3)
                if key in props:
                    dup_fixes += 1  # Duplicate found — keep last
                else:
                    prop_order.append(key)
                props[key] = (val, f"{indent}:{key}: {val}")
            else:
                # Continuation line for multiline property
                if prop_order:
                    last_key = prop_order[-1]
                    old_val, old_line = props[last_key]
                    props[last_key] = (old_val, old_line + "\n" + prop_line)
            j += 1

        # Emit deduplicated block
        output.append(line)  # :PROPERTIES:
        for key in prop_order:
            _, prop_line = props[key]
            output.append(prop_line)
        if j < len(lines):
            output.append(lines[j])  # :END:
        i = j + 1
    else:
        output.append(line)
        i += 1

result = "\n".join(output)
fp.write_text(result)
print(f"Fixed {dup_fixes} duplicate properties, {orphan_fixes} orphaned lines")
print(f"File: {len(lines)} -> {len(output)} lines")
