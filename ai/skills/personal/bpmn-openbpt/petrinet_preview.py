#!/usr/bin/env python3
"""Render a native .ptn file to an SVG (places=circles w/ tokens, transitions=boxes,
silent=filled, arc weights shown). Usage: python3 petrinet_preview.py net.ptn -> net.svg"""
import xml.etree.ElementTree as ET, sys, math
P = "{http://bpt-lab.org/schemas/ptn}"; D = "{http://bpt-lab.org/schemas/ptnDi}"
DC = "{https://www.omg.org/spec/BPMN/20100501/DC.xsd}"

r = ET.parse(sys.argv[1]).getroot()
model = r.find(P + "model")
kind, nm, mark, silent, weight = {}, {}, {}, {}, {}
for e in model:
    t = e.tag.replace(P, ""); i = e.get("id")
    if t in ("place", "transition"):
        kind[i] = t; n = e.find(P + "name"); nm[i] = n.text if n is not None else i
        if t == "place":
            im = e.find(P + "initialMarking"); mark[i] = int(im.text) if im is not None else 0
        else:
            silent[i] = e.get("isSilent") == "true"
    elif t == "arc":
        ins = e.find(P + "inscription")
        if ins is not None: weight[i] = ins.text

shapes, edges = [], []
plane = r.find(D + "diagram").find(D + "plane")
for s in plane:
    tt = s.tag.replace(D, "")
    if tt == "diagramShape":
        b = s.find(DC + "Bounds")
        shapes.append((s.get("modelElement"), float(b.get("x")), float(b.get("y")),
                       float(b.get("width")), float(b.get("height"))))
    else:
        wps = [(float(w.get("x")), float(w.get("y"))) for w in s.findall(D + "waypoint")]
        edges.append((s.get("modelElement"), wps))

xs = [x for _, x, y, w, h in shapes]; ys = [y for _, x, y, w, h in shapes]
minx, miny = min(xs) - 40, min(ys) - 50
maxx = max(x + w for _, x, y, w, h in shapes) + 60; maxy = max(y + h for _, x, y, w, h in shapes) + 50
W, H = maxx - minx, maxy - miny
o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx} {miny} {W} {H}" width="{W}" height="{H}" font-family="Helvetica" font-size="11">',
     f'<rect x="{minx}" y="{miny}" width="{W}" height="{H}" fill="white"/>']
def esc(s): return (s or "").replace("&", "&amp;").replace("<", "&lt;")

for aid, wps in edges:
    pts = " ".join(f"{x},{y}" for x, y in wps)
    o.append(f'<polyline points="{pts}" fill="none" stroke="#333" stroke-width="1.4"/>')
    (x1, y1), (x2, y2) = wps[-2], wps[-1]; a = math.atan2(y2 - y1, x2 - x1)
    for da in (2.6, -2.6):
        o.append(f'<line x1="{x2}" y1="{y2}" x2="{x2+9*math.cos(a+da)}" y2="{y2+9*math.sin(a+da)}" stroke="#333" stroke-width="1.4"/>')
    if aid in weight:                       # arc weight label
        mx = (wps[0][0] + wps[-1][0]) / 2; my = (wps[0][1] + wps[-1][1]) / 2
        o.append(f'<rect x="{mx-8}" y="{my-16}" width="16" height="14" fill="white"/>')
        o.append(f'<text x="{mx}" y="{my-5}" text-anchor="middle" font-weight="bold" fill="#b00">{esc(weight[aid])}</text>')

for id, x, y, w, h in shapes:
    cx, cy = x + w / 2, y + h / 2
    if kind.get(id) == "place":
        o.append(f'<circle cx="{cx}" cy="{cy}" r="{w/2}" fill="white" stroke="#333" stroke-width="1.5"/>')
        m = mark.get(id, 0)
        if m == 1: o.append(f'<circle cx="{cx}" cy="{cy}" r="6" fill="#111"/>')
        elif m > 1: o.append(f'<text x="{cx}" y="{cy+4}" text-anchor="middle" font-weight="bold" fill="#111">{m}</text>')
    else:
        fill = "#444" if silent.get(id) else "white"
        o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="#333"/>')
    o.append(f'<text x="{cx}" y="{y+h+13}" text-anchor="middle" fill="#111">{esc(nm.get(id))}</text>')

out = sys.argv[1].rsplit(".", 1)[0] + ".svg"
o.append("</svg>"); open(out, "w").write("\n".join(o)); print("wrote", out, int(W), "x", int(H))
