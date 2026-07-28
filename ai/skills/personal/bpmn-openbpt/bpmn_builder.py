#!/usr/bin/env python3
"""bpmn_builder — tiny library to emit importable BPMN 2.0 XML (with DI) for
app.openbpt.org (bpmn-js / bpmn-moddle). Declare pools, lanes, nodes, flows,
data objects; call .build() to get XML. Waypoints auto-route if not given.

Coordinate model: every shape has absolute (x, y, w, h); origin top-left, x→right,
y→down. Keep the happy path on one horizontal band and branch vertically.
Validate the result with:  node validate.mjs <file.bpmn>   (see the skill folder).
"""
import xml.etree.ElementTree as ET
from xml.dom import minidom

NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
def _q(p, t): return f"{{{NS[p]}}}{t}"

# event-definition child tag per shorthand
_EVDEF = {"message": "messageEventDefinition", "timer": "timerEventDefinition",
          "signal": "signalEventDefinition", "error": "errorEventDefinition",
          "terminate": "terminateEventDefinition"}
_GATEWAYS = {"exclusiveGateway", "parallelGateway", "eventBasedGateway", "inclusiveGateway"}
_SUBPROCS = {"subProcess", "adHocSubProcess", "transaction"}


class Bpmn:
    def __init__(self, defs_id="Definitions_1", target_ns="http://bpmn.io/schema/bpmn",
                 process_id="Process_1", executable=False):
        self.defs_id, self.target_ns = defs_id, target_ns
        self.process_id, self.executable = process_id, executable
        self.geom = {}                 # id -> (x, y, w, h)
        self.nodes = {}                # id -> dict(kind, name, evdef, timer, signal_ref,
                                       #            interrupting, parent, default, attached_to,
                                       #            cancel, expanded)
        self.order = []                # node ids in insertion order
        self.flows = []                # dict(id, src, tgt, name, cond, wps, hint)
        self.msgs = []                 # dict(id, src, tgt, wps)
        self.dassoc = []               # dict(id, owner, kind in/out, ref, wps)
        self.pools = []                # dict(id, name, geom, proc, blackbox)
        self.lanes = []                # dict(id, name, geom, refs)
        self.signals = []              # (id, name)
        self.messages = []             # (id, name)
        self.dataobjects = []          # id
        self.datarefs = []             # dict(id, name, geom, ref)

    # ---- declarations -------------------------------------------------------
    def pool(self, id, name, x, y, w, h, process=None, blackbox=False):
        self.geom[id] = (x, y, w, h)
        self.pools.append(dict(id=id, name=name, proc=process, blackbox=blackbox))
        return id

    def lane(self, id, name, x, y, w, h, refs):
        self.geom[id] = (x, y, w, h)
        self.lanes.append(dict(id=id, name=name, refs=list(refs)))
        return id

    def node(self, id, kind, x, y, w, h, name="", evdef=None, timer=None,
             signal_ref=None, interrupting=None, parent=None, default=None,
             attached_to=None, cancel=None, expanded=None):
        self.geom[id] = (x, y, w, h)
        self.nodes[id] = dict(kind=kind, name=name, evdef=evdef, timer=timer,
                              signal_ref=signal_ref, interrupting=interrupting,
                              parent=parent, default=default, attached_to=attached_to,
                              cancel=cancel, expanded=expanded)
        self.order.append(id)
        return id

    def signal(self, id, name): self.signals.append((id, name)); return id
    def message_def(self, id, name): self.messages.append((id, name)); return id
    def data_object(self, id): self.dataobjects.append(id); return id

    def data_ref(self, id, name, x, y, w, h, ref):
        self.geom[id] = (x, y, w, h)
        self.datarefs.append(dict(id=id, name=name, ref=ref))
        return id

    def flow(self, id, src, tgt, name="", cond=None, wps=None, hint=None):
        self.flows.append(dict(id=id, src=src, tgt=tgt, name=name, cond=cond, wps=wps, hint=hint))
        return id

    def message(self, id, src, tgt, wps=None):
        self.msgs.append(dict(id=id, src=src, tgt=tgt, wps=wps)); return id

    def data_assoc(self, id, owner, kind, ref, wps=None):
        assert kind in ("in", "out")
        self.dassoc.append(dict(id=id, owner=owner, kind=kind, ref=ref, wps=wps)); return id

    # ---- geometry helpers ---------------------------------------------------
    def _cx(self, i): x, y, w, h = self.geom[i]; return x + w / 2
    def _cy(self, i): x, y, w, h = self.geom[i]; return y + h / 2
    def _sides(self, i):
        x, y, w, h = self.geom[i]
        return dict(l=(x, y + h / 2), r=(x + w, y + h / 2),
                    t=(x + w / 2, y), b=(x + w / 2, y + h / 2 * 0 + h))

    def _route(self, src, tgt, hint):
        """Return a waypoint list. hint in {None,H,V,VH,HV}."""
        sx, sy, sw, sh = self.geom[src]; tx, ty, tw, th = self.geom[tgt]
        scx, scy, tcx, tcy = sx + sw / 2, sy + sh / 2, tx + tw / 2, ty + th / 2
        dx, dy = tcx - scx, tcy - scy
        if hint is None:
            if abs(dy) < 12: hint = "H"
            elif abs(dx) < 12: hint = "V"
            else: hint = "VH" if abs(dy) >= abs(dx) else "HV"
        if hint == "H":
            if tx >= sx + sw: return [(sx + sw, scy), (tx, tcy)]
            return [(sx, scy), (tx + tw, tcy)]
        if hint == "V":
            if ty >= sy + sh: return [(scx, sy + sh), (tcx, ty)]
            return [(scx, sy), (tcx, ty + th)]
        if hint == "VH":                       # vertical first, then horizontal
            ys = sy + sh if dy > 0 else sy
            txp = tx if dx > 0 else tx + tw
            return [(scx, ys), (scx, tcy), (txp, tcy)]
        # HV: horizontal first, then vertical
        xs = sx + sw if dx > 0 else sx
        typ = ty if dy > 0 else ty + th
        return [(xs, scy), (tcx, scy), (tcx, typ)]

    # ---- emit ---------------------------------------------------------------
    def build(self, pretty=True):
        self._subproc_el = {}
        for p, u in NS.items(): ET.register_namespace(p, u)
        defs = ET.Element(_q("bpmn", "definitions"),
                          {"id": self.defs_id, "targetNamespace": self.target_ns})
        for sid, sname in self.signals:
            ET.SubElement(defs, _q("bpmn", "signal"), {"id": sid, "name": sname})
        for mid, mname in self.messages:
            ET.SubElement(defs, _q("bpmn", "message"), {"id": mid, "name": mname})

        collab = ET.SubElement(defs, _q("bpmn", "collaboration"), {"id": "Collaboration_1"})
        for p in self.pools:
            a = {"id": p["id"], "name": p["name"]}
            if p["proc"]: a["processRef"] = p["proc"]
            ET.SubElement(collab, _q("bpmn", "participant"), a)
        for m in self.msgs:
            ET.SubElement(collab, _q("bpmn", "messageFlow"),
                          {"id": m["id"], "sourceRef": m["src"], "targetRef": m["tgt"]})

        proc = ET.SubElement(defs, _q("bpmn", "process"),
                             {"id": self.process_id,
                              "isExecutable": "true" if self.executable else "false"})
        if self.lanes:
            ls = ET.SubElement(proc, _q("bpmn", "laneSet"), {"id": "LaneSet_1"})
            for ln in self.lanes:
                le = ET.SubElement(ls, _q("bpmn", "lane"), {"id": ln["id"], "name": ln["name"]})
                for r in ln["refs"]:
                    ET.SubElement(le, _q("bpmn", "flowNodeRef")).text = r
        for oid in self.dataobjects:
            ET.SubElement(proc, _q("bpmn", "dataObject"), {"id": oid})
        for r in self.datarefs:
            ET.SubElement(proc, _q("bpmn", "dataObjectReference"),
                          {"id": r["id"], "name": r["name"], "dataObjectRef": r["ref"]})

        # flow nodes (top level + nested inside sub-processes)
        for nid in self.order:
            if self.nodes[nid]["parent"]: continue
            self._emit_node(proc, nid)
        # sequence flows: nested ones inside their sub-process, rest at process level
        for f in self.flows:
            par = self.nodes[f["src"]]["parent"]
            parent_el = self._subproc_el.get(par, proc) if par else proc
            self._emit_flow(parent_el, f)

        self._emit_di(defs)
        raw = ET.tostring(defs, encoding="utf-8")
        if pretty:
            return minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8")
        return raw

    def _emit_node(self, parent, nid):
        n = self.nodes[nid]; a = {"id": nid}
        if n["name"]: a["name"] = n["name"]
        if nid == getattr(self, "_none", None): pass
        if n["default"]: a["default"] = n["default"]
        if n["kind"] in _SUBPROCS and n["kind"] == "adHocSubProcess": pass
        if n["kind"] == "subProcess" and self._is_event_sub(nid): a["triggeredByEvent"] = "true"
        if n["kind"] == "startEvent" and n["interrupting"] is False: a["isInterrupting"] = "false"
        if n["kind"] == "boundaryEvent":
            a["attachedToRef"] = n["attached_to"]
            a["cancelActivity"] = "true" if n["cancel"] is not False else "false"
        el = ET.SubElement(parent, _q("bpmn", n["kind"]), a)
        # data associations live inside the owning activity
        for d in self.dassoc:
            if d["owner"] != nid: continue
            if d["kind"] == "in":
                de = ET.SubElement(el, _q("bpmn", "dataInputAssociation"), {"id": d["id"]})
                ET.SubElement(de, _q("bpmn", "sourceRef")).text = d["ref"]
            else:
                de = ET.SubElement(el, _q("bpmn", "dataOutputAssociation"), {"id": d["id"]})
                ET.SubElement(de, _q("bpmn", "targetRef")).text = d["ref"]
        # event definition
        if n["evdef"]:
            ed = ET.SubElement(el, _q("bpmn", _EVDEF[n["evdef"]]))
            if n["evdef"] == "signal" and n["signal_ref"]:
                ed.set("signalRef", n["signal_ref"])
            if n["evdef"] == "timer" and n["timer"]:
                td = ET.SubElement(ed, _q("bpmn", "timeDuration"))
                td.set(_q("xsi", "type"), "bpmn:tFormalExpression"); td.text = n["timer"]
        # nested children for sub-processes
        if n["kind"] in _SUBPROCS:
            self._subproc_el = getattr(self, "_subproc_el", {}); self._subproc_el[nid] = el
            for cid in self.order:
                if self.nodes[cid]["parent"] == nid:
                    self._emit_node(el, cid)
        return el

    def _is_event_sub(self, nid):
        # a subProcess is an event sub-process if it contains a start event child
        for cid in self.order:
            c = self.nodes[cid]
            if c["parent"] == nid and c["kind"] == "startEvent":
                return True
        return False

    def _emit_flow(self, parent, f):
        a = {"id": f["id"], "sourceRef": f["src"], "targetRef": f["tgt"]}
        if f["name"]: a["name"] = f["name"]
        el = ET.SubElement(parent, _q("bpmn", "sequenceFlow"), a)
        if f["cond"] is not None:
            ce = ET.SubElement(el, _q("bpmn", "conditionExpression"))
            ce.set(_q("xsi", "type"), "bpmn:tFormalExpression")
            ce.text = f["cond"] if isinstance(f["cond"], str) else (f["name"] or "true")

    def _emit_di(self, defs):
        dia = ET.SubElement(defs, _q("bpmndi", "BPMNDiagram"), {"id": "Diagram_1"})
        plane = ET.SubElement(dia, _q("bpmndi", "BPMNPlane"),
                              {"id": "Plane_1", "bpmnElement": "Collaboration_1"})

        def shape(eid, extra=None):
            x, y, w, h = self.geom[eid]
            at = {"id": eid + "_di", "bpmnElement": eid}
            if extra: at.update(extra)
            s = ET.SubElement(plane, _q("bpmndi", "BPMNShape"), at)
            ET.SubElement(s, _q("dc", "Bounds"),
                          {"x": str(x), "y": str(y), "width": str(w), "height": str(h)})

        def edge(eid, wps):
            e = ET.SubElement(plane, _q("bpmndi", "BPMNEdge"),
                              {"id": eid + "_di", "bpmnElement": eid})
            for (x, y) in wps:
                ET.SubElement(e, _q("di", "waypoint"), {"x": str(int(x)), "y": str(int(y))})

        for p in self.pools: shape(p["id"], {"isHorizontal": "true"})
        for ln in self.lanes: shape(ln["id"], {"isHorizontal": "true"})
        for nid in self.order:
            n = self.nodes[nid]; extra = {}
            if n["kind"] in _GATEWAYS:
                extra["isMarkerVisible"] = "true" if n["kind"] == "exclusiveGateway" else "false"
            if n["kind"] in _SUBPROCS:
                extra["isExpanded"] = "false" if n["expanded"] is False else "true"
            shape(nid, extra or None)
        for r in self.datarefs: shape(r["id"])
        for f in self.flows:
            edge(f["id"], f["wps"] if f["wps"] else self._route(f["src"], f["tgt"], f["hint"]))
        for d in self.dassoc:
            if d["wps"]:
                edge(d["id"], d["wps"])
            else:
                if d["kind"] == "in":
                    edge(d["id"], self._route(d["ref"], d["owner"], None))
                else:
                    edge(d["id"], self._route(d["owner"], d["ref"], None))
        for m in self.msgs:
            edge(m["id"], m["wps"] if m["wps"] else self._route(m["src"], m["tgt"], "V"))
