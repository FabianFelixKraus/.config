import xml.etree.ElementTree as ET, sys
B="{http://www.omg.org/spec/BPMN/20100524/MODEL}"
BD="{http://www.omg.org/spec/BPMN/20100524/DI}"
DC="{http://www.omg.org/spec/DD/20100524/DC}"
DI="{http://www.omg.org/spec/DD/20100524/DI}"
r=ET.parse(sys.argv[1]).getroot()
tag={e.get("id"):e.tag.replace(B,"") for e in r.iter() if e.get("id")}
name={e.get("id"):(e.get("name") or "") for e in r.iter() if e.get("id")}
shapes=[]; edges=[]
for s in r.iter(BD+"BPMNShape"):
    b=s.find(DC+"Bounds"); shapes.append((s.get("bpmnElement"),float(b.get("x")),float(b.get("y")),float(b.get("width")),float(b.get("height"))))
for e in r.iter(BD+"BPMNEdge"):
    wps=[(float(w.get("x")),float(w.get("y"))) for w in e.findall(DI+"waypoint")]
    edges.append((e.get("bpmnElement"),wps))
xs=[x for _,x,y,w,h in shapes]+[x for _,x,y,w,h in shapes]
minx=min(x for _,x,y,w,h in shapes)-40; miny=min(y for _,x,y,w,h in shapes)-40
maxx=max(x+w for _,x,y,w,h in shapes)+40; maxy=max(y+h for _,x,y,w,h in shapes)+40
W,H=maxx-minx,maxy-miny
out=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx} {miny} {W} {H}" width="{W}" height="{H}" font-family="Helvetica" font-size="12">']
out.append(f'<rect x="{minx}" y="{miny}" width="{W}" height="{H}" fill="white"/>')
def esc(s): return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
def label(cx,cy,t,dy=0):
    for i,line in enumerate(t.split("\n")):
        out.append(f'<text x="{cx}" y="{cy+dy+i*13}" text-anchor="middle">{esc(line)}</text>')
# pools/lanes first
for id,x,y,w,h in shapes:
    k=tag.get(id)
    if k in ("participant","lane"):
        fill="#f7f7f9" if k=="lane" else "#eef1f6"
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="#888"/>')
        out.append(f'<text x="{x+14}" y="{y+h/2}" text-anchor="middle" transform="rotate(-90 {x+14} {y+h/2})" font-weight="bold">{esc(name.get(id,""))}</text>')
# edges
for id,wps in edges:
    k=tag.get(id,"")
    dash="" 
    if k=="messageFlow": dash=' stroke-dasharray="8 4"'
    if "Association" in k: dash=' stroke-dasharray="2 3"'
    pts=" ".join(f"{x},{y}" for x,y in wps)
    out.append(f'<polyline points="{pts}" fill="none" stroke="#333"{dash} stroke-width="1.4"/>')
    # arrowhead
    if len(wps)>=2:
        (x1,y1),(x2,y2)=wps[-2],wps[-1]
        import math; a=math.atan2(y2-y1,x2-x1)
        for da in (2.6,-2.6):
            out.append(f'<line x1="{x2}" y1="{y2}" x2="{x2+10*math.cos(a+da)}" y2="{y2+10*math.sin(a+da)}" stroke="#333" stroke-width="1.4"/>')
    if name.get(id):
        mx,my=wps[len(wps)//2]; out.append(f'<text x="{mx}" y="{my-4}" text-anchor="middle" fill="#0055aa">{esc(name[id])}</text>')
# nodes on top
for id,x,y,w,h in shapes:
    k=tag.get(id)
    if k in ("participant","lane"): continue
    cx,cy=x+w/2,y+h/2
    if k and "Gateway" in k:
        out.append(f'<polygon points="{cx},{y} {x+w},{cy} {cx},{y+h} {x},{cy}" fill="white" stroke="#333"/>')
        label(cx,y+h+13,name.get(id,""))
    elif k and ("Event" in k or k in("startEvent","endEvent")):
        sw=3 if k=="endEvent" else 1.5
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{w/2}" fill="white" stroke="#333" stroke-width="{sw}"/>')
        label(cx,y+h+13,name.get(id,""))
    elif k=="dataObjectReference":
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="white" stroke="#333"/>')
        label(cx,y+h+12,name.get(id,""))
    else: # task / subprocess
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="white" stroke="#333"/>')
        label(cx,cy+4,name.get(id,""))
out.append("</svg>")
open("layout.svg","w").write("\n".join(out))
print("wrote layout.svg", int(W),"x",int(H))
