#!/usr/bin/env python3
"""Clean a GPX route: drop duplicate points, densify to a fixed spacing, rename the track.

Sparse course files (race organisers often publish one point every ~100 m) trigger
false "off course" alerts, because watches measure deviation as the distance to the
nearest *course point*, not to the nearest segment. Densifying removes that class of
false alarm. It does not move the line onto the road — only map-matching does that.

Usage:
    python3 gpx_clean.py IN.gpx OUT.gpx [--spacing 10] [--name "Course name"]
"""
import argparse
import math
import re
import sys

TRKPT = re.compile(
    r'<trkpt\s+lat="([-0-9.]+)"\s+lon="([-0-9.]+)"\s*>(?:\s*<ele>([-0-9.]+)</ele>)?',
    re.I,
)


def haversine(a, b):
    r = 6371000.0
    dlat = math.radians(b[0] - a[0])
    dlon = math.radians(b[1] - a[1])
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


def parse(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    pts = [(float(lat), float(lon), float(ele) if ele else None)
           for lat, lon, ele in TRKPT.findall(text)]
    if not pts:
        sys.exit(f"no <trkpt> found in {path}")
    return pts


def dedupe(pts, eps=0.5):
    out = [pts[0]]
    for p in pts[1:]:
        if haversine(out[-1], p) > eps:
            out.append(p)
    return out


def densify(pts, spacing):
    out = []
    for a, b in zip(pts, pts[1:]):
        out.append(a)
        steps = int(haversine(a, b) // spacing)
        for i in range(1, steps):
            t = i / steps
            ele = None
            if a[2] is not None and b[2] is not None:
                ele = a[2] + (b[2] - a[2]) * t
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, ele))
    out.append(pts[-1])
    return out


def length(pts):
    return sum(haversine(a, b) for a, b in zip(pts, pts[1:]))


def write(path, pts, name):
    body = []
    for lat, lon, ele in pts:
        body.append(f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}">')
        if ele is not None:
            body.append(f"        <ele>{ele:.1f}</ele>")
        body.append("      </trkpt>")
    esc = name.replace("&", "&amp;").replace("<", "&lt;")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="datacore gpx_clean.py"\n'
            '     xmlns="http://www.topografix.com/GPX/1/1"\n'
            '     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
            '     xsi:schemaLocation="http://www.topografix.com/GPX/1/1 '
            'http://www.topografix.com/GPX/1/1/gpx.xsd">\n'
            f"  <metadata><name>{esc}</name></metadata>\n"
            f"  <trk>\n    <name>{esc}</name>\n    <trkseg>\n"
            + "\n".join(body)
            + "\n    </trkseg>\n  </trk>\n</gpx>\n"
        )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source")
    ap.add_argument("dest")
    ap.add_argument("--spacing", type=float, default=10.0, help="metres between points")
    ap.add_argument("--name", default=None, help="track name to write")
    args = ap.parse_args()

    raw = parse(args.source)
    clean = dedupe(raw)
    dense = densify(clean, args.spacing)
    write(args.dest, dense, args.name or f"{args.source} (cleaned)")

    print(f"in   {len(raw):>5} pts  {length(raw)/1000:6.2f} km")
    print(f"out  {len(dense):>5} pts  {length(dense)/1000:6.2f} km  "
          f"(~{length(dense)/max(len(dense)-1,1):.0f} m spacing)")


if __name__ == "__main__":
    main()
