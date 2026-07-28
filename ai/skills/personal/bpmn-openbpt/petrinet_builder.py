#!/usr/bin/env python3
"""petrinet_builder — emit a Place/Transition net in openBPT's NATIVE `ptn` moddle
format (loaded via importXML) AND in standard PNML 2009 (loaded via importPNML).

Nodes carry a centre (cx, cy); diagram-interchange bounds and 2-point arc waypoints
are computed automatically (openBPT is diagram-js based and needs the DI to render).

    from petrinet_builder import PetriNet
    pn = PetriNet("My net")
    pn.place("i", "i", 80, 300, tokens=1)        # source place, 1 token
    pn.transition("t", "Do work", 170, 300)      # labelled transition
    pn.transition("g", "", 260, 300, silent=True)# silent (black) transition = a gateway
    pn.place("o", "o", 350, 300)                  # sink place
    pn.arc("i", "t"); pn.arc("t", "g"); pn.arc("g", "o")   # arc ids auto-generated
    pn.save("net.ptn", "net.pnml")

Validate the native output against openBPT's own schema (see validate_ptn.mjs):
    npm i moddle moddle-xml && node validate_ptn.mjs net.ptn      # want: warnings: 0
Preview the layout:
    python3 petrinet_preview.py net.ptn                           # -> net.svg
"""
import xml.etree.ElementTree as ET
from xml.dom import minidom

NS = {
    "ptn": "http://bpt-lab.org/schemas/ptn",
    "ptnDi": "http://bpt-lab.org/schemas/ptnDi",
    "dc": "https://www.omg.org/spec/BPMN/20100501/DC.xsd",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
PLACE_SIZE = (36, 36)
TRANS_SIZE = (46, 32)

def _q(p, t): return f"{{{NS[p]}}}{t}"
def _sign(v): return (v > 0) - (v < 0)


class PetriNet:
    def __init__(self, name="Petri net", model_id="model_1"):
        self.name, self.model_id = name, model_id
        self.nodes = {}          # id -> dict(kind, name, cx, cy, silent, tokens)
        self.order = []
        self.arcs = []           # dict(id, src, tgt)
        self._auto = 0

    # -- declarations --------------------------------------------------------
    def place(self, id, name, cx, cy, tokens=0):
        self.nodes[id] = dict(kind="place", name=name, cx=cx, cy=cy, silent=False, tokens=tokens)
        self.order.append(id); return id

    def transition(self, id, name, cx, cy, silent=False):
        self.nodes[id] = dict(kind="trans", name=name, cx=cx, cy=cy, silent=silent, tokens=0)
        self.order.append(id); return id

    def arc(self, src, tgt, id=None, weight=1):
        assert src in self.nodes and tgt in self.nodes, ("unknown node in arc", src, tgt)
        if id is None:
            self._auto += 1; id = f"a{self._auto}"
        self.arcs.append(dict(id=id, src=src, tgt=tgt, weight=weight)); return id

    # -- geometry ------------------------------------------------------------
    def _box(self, id):
        n = self.nodes[id]; w, h = PLACE_SIZE if n["kind"] == "place" else TRANS_SIZE
        return (n["cx"] - w / 2, n["cy"] - h / 2, w, h, n["cx"], n["cy"])

    def _anchor(self, a, b):
        ax, ay, aw, ah, acx, acy = self._box(a); bcx, bcy = self._box(b)[4], self._box(b)[5]
        dx, dy = bcx - acx, bcy - acy
        if abs(dx) >= abs(dy):
            x = acx + _sign(dx) * aw / 2; y = acy + (dy / abs(dx)) * (aw / 2) if dx else acy
        else:
            y = acy + _sign(dy) * ah / 2; x = acx + (dx / abs(dy)) * (ah / 2) if dy else acx
        return (round(x), round(y))

    # -- native ptn (importXML) ---------------------------------------------
    def build(self, pretty=True):
        for p, u in NS.items(): ET.register_namespace(p, u)
        defs = ET.Element(_q("ptn", "definitions"),
                          {"id": "defs_1", "exporter": "petrinet_builder", "exporterVersion": "1"})
        model = ET.SubElement(defs, _q("ptn", "model"), {"id": self.model_id})
        ET.SubElement(model, _q("ptn", "name")).text = self.name
        for id in self.order:
            n = self.nodes[id]
            if n["kind"] == "place":
                el = ET.SubElement(model, _q("ptn", "place"), {"id": id})
                ET.SubElement(el, _q("ptn", "name")).text = n["name"]
                if n["tokens"]:
                    ET.SubElement(el, _q("ptn", "initialMarking")).text = str(n["tokens"])
            else:
                attrs = {"id": id}
                if n["silent"]: attrs["isSilent"] = "true"
                el = ET.SubElement(model, _q("ptn", "transition"), attrs)
                ET.SubElement(el, _q("ptn", "name")).text = n["name"]
        for a in self.arcs:
            el = ET.SubElement(model, _q("ptn", "arc"), {"id": a["id"], "source": a["src"], "target": a["tgt"]})
            if a.get("weight", 1) != 1:
                ET.SubElement(el, _q("ptn", "inscription")).text = str(a["weight"])

        dia = ET.SubElement(defs, _q("ptnDi", "diagram"), {"id": self.model_id + "_di"})
        plane = ET.SubElement(dia, _q("ptnDi", "plane"),
                              {"id": self.model_id + "_plane", "modelElement": self.model_id})
        for id in self.order:
            x, y, w, h, cx, cy = self._box(id)
            sh = ET.SubElement(plane, _q("ptnDi", "diagramShape"), {"id": id + "_di", "modelElement": id})
            ET.SubElement(sh, _q("dc", "Bounds"),
                          {"x": str(round(x)), "y": str(round(y)), "width": str(w), "height": str(h)})
        for a in self.arcs:
            ed = ET.SubElement(plane, _q("ptnDi", "diagramEdge"),
                               {"id": a["id"] + "_di", "modelElement": a["id"],
                                "sourceElement": a["src"], "targetElement": a["tgt"]})
            for (x, y) in (self._anchor(a["src"], a["tgt"]), self._anchor(a["tgt"], a["src"])):
                wp = ET.SubElement(ed, _q("ptnDi", "waypoint"), {"x": str(x), "y": str(y)})
                wp.set(_q("xsi", "type"), "dc:Point")
        raw = ET.tostring(defs, encoding="utf-8")
        return minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8") if pretty else raw

    # -- standard PNML (importPNML) -----------------------------------------
    def to_pnml(self, pretty=True):
        pn = ET.Element("pnml", {"xmlns": "http://www.pnml.org/version-2009/grammar/pnml"})
        net = ET.SubElement(pn, "net", {"id": "net_1", "type": "http://www.pnml.org/version-2009/grammar/ptnet"})
        ET.SubElement(ET.SubElement(net, "name"), "text").text = self.name
        page = ET.SubElement(net, "page", {"id": "page1"})
        for id in self.order:
            n = self.nodes[id]
            el = ET.SubElement(page, "place" if n["kind"] == "place" else "transition", {"id": id})
            ET.SubElement(ET.SubElement(el, "name"), "text").text = n["name"] or id
            ET.SubElement(ET.SubElement(el, "graphics"), "position", {"x": str(n["cx"]), "y": str(n["cy"])})
            if n["kind"] == "place" and n["tokens"]:
                ET.SubElement(ET.SubElement(el, "initialMarking"), "text").text = str(n["tokens"])
        for a in self.arcs:
            el = ET.SubElement(page, "arc", {"id": a["id"], "source": a["src"], "target": a["tgt"]})
            if a.get("weight", 1) != 1:
                ET.SubElement(ET.SubElement(el, "inscription"), "text").text = str(a["weight"])
        raw = ET.tostring(pn, encoding="utf-8")
        return minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8") if pretty else raw

    def save(self, path_ptn, path_pnml=None):
        with open(path_ptn, "wb") as f: f.write(self.build())
        if path_pnml:
            with open(path_pnml, "wb") as f: f.write(self.to_pnml())
        return path_ptn
