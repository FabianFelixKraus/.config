---
name: bpmn-openbpt
description: >
  Generate diagrams that import cleanly into openBPT (app.openbpt.org) for Fabian's POSE
  course exercises — BPMN process models AND Place/Transition (workflow) nets. Use when
  asked to "model this process as a BPMN diagram", produce a .bpmn / .ptn / .pnml file,
  translate a BPMN to a workflow net, or make a Petri net importable/analysable in openBPT.
  Ships two Python builder libraries (bpmn_builder.py, petrinet_builder.py) that emit valid
  XML + diagram interchange, moddle validators, and SVG preview renderers.
  Triggers: BPMN, openBPT, app.openbpt.org, "model this process", .bpmn, .ptn, PNML, Petri
  net, workflow net, translate BPMN to Petri net, soundness/reachability, lanes, event-based
  gateway, ad-hoc subprocess.
---

# Authoring importable BPMN for openBPT

## The one fact that saves an hour
**openBPT's BPMN modeler (`bptlab/openbpt-modeler-bpmn`) is bpmn-js / bpmn-moddle based.
It imports/exports BPMN 2.0 XML (`.bpmn`) — NOT JSON.** If a user asks for "a JSON file for
openBPT", they mean *the importable standard file*, which is BPMN 2.0 XML. Deliver `.bpmn`.
(JSON in that ecosystem is only the internal `moddle` *schema*, never a model exchange format.
The Petri-net modeler uses PNML; the choreography modeler uses BPMN2 XML.)

A valid file MUST contain **both** the semantic model (`bpmn:process` / `bpmn:collaboration`)
**and** diagram interchange (`bpmndi:BPMNPlane` with a `BPMNShape` for every node and a
`BPMNEdge`, each with ≥2 waypoints, for every flow). bpmn-js does **not** auto-layout on
import — missing/!broken DI ⇒ blank canvas or dropped elements. Always emit full DI.

## Workflow
1. Design the model on paper first (nodes, flows, which construct per requirement).
2. Lay it out on a grid: **happy path on one horizontal band**, branches drop vertically,
   loops routed through a **channel** below the lane (a shared y just under the nodes) so
   backward edges don't cross boxes. Pick coordinates in gaps between shapes.
3. Build it with `bpmn_builder.py` (below). Prefer explicit `wps=` for loop/branch edges;
   let the router auto-place simple forward edges.
4. **Validate**: `cd` to a folder with bpmn-moddle installed, then
   `node validate.mjs file.bpmn` — must say `PARSE OK … warnings: 0`.
5. **Eyeball**: `python3 preview.py file.bpmn` → `layout.svg` (open in browser), or rasterize
   with resvg (see below). Check for overlaps the semantic validator can't see.
6. Save the `.bpmn` to `~/Documents/vault/my_vault/bpmn-exercises/` and tell Fabian to import
   it in app.openbpt.org (Open/Import file button, or drag onto canvas).

## Builder API (`bpmn_builder.py`)
```python
from bpmn_builder import Bpmn
b = Bpmn(defs_id="Definitions_x", process_id="Process_x")
b.pool(id, name, x,y,w,h, process=None, blackbox=False)   # blackbox pool = no process
b.lane(id, name, x,y,w,h, refs=[nodeIds...])              # refs = top-level flow nodes in the lane
b.node(id, kind, x,y,w,h, name="", evdef=None, timer="P1W", signal_ref=None,
       interrupting=False, parent=SubId, default="F4", attached_to=SubId, cancel=True,
       expanded=False)
#   kind: startEvent endEvent intermediateCatchEvent intermediateThrowEvent boundaryEvent
#         task exclusiveGateway eventBasedGateway parallelGateway subProcess adHocSubProcess
#   evdef: message | timer | signal | error | terminate
#   parent=SubId nests a node inside a (adHoc)subProcess; expanded=False collapses it
b.signal(id, name); b.data_object(id)
b.data_ref(id, name, x,y,w,h, ref=dataObjectId)           # data object reference w/ state in the name
b.flow(id, src, tgt, name="", cond="expr", wps=[(x,y)...], hint="H|V|VH|HV")  # omit wps to auto-route
b.message(id, src, tgt, wps=[...])
b.data_assoc(id, owner=taskId, kind="in|out", ref=dataRefId, wps=[...])
open("out.bpmn","wb").write(b.build())
```
Rules the builder enforces / you must respect:
- A **default flow must NOT have `cond`** (set `default=` on the gateway, give the default flow a
  `name` but no `cond`; give every *other* branch a `cond`). The validator checks this.
- Data associations live inside the owning activity; `data_ref` shows state via the name,
  e.g. `"Application [checked]"` (bpmn-js doesn't render `dataState` reliably).
- Put a node's id in exactly one `lane(refs=...)`; children of a sub-process are NOT lane refs.

## Construct recipes (map requirement → BPMN)
- "process starts when X received" → **message start event** + black-box pool + message flow in.
- "requested from / sent to an external party" → **black-box pool** + `message()` flow.
- "wait N; if meanwhile Y happens, …" → **eventBasedGateway** → catch(Y) vs `timer` catch.
  Guideline: an event-based gateway ALWAYS needs a timer branch (deadlock prevention).
- "notify other process instances" / broadcast → **signal** throw; other instances **signal** catch
  (signals broadcast; messages are point-to-point).
- "at any time during the process, X can happen" → **interrupting event sub-process**
  (`subProcess` with a message `startEvent` child, `triggeredByEvent` auto-set).
- "if not finished by <deadline>" on an activity → **interrupting timer boundaryEvent**
  (`boundaryEvent`, `attached_to=Sub`, `evdef="timer"`).
- "may involve different tasks, in any order / optional" → **adHocSubProcess** with child tasks
  and NO sequence flows between them.
- "can be complex, abstract from it" → **collapsed subProcess** (`expanded=False`, no children).
- roles (secretary, dev team, designer, manager) → **lanes** in one pool.
- "put X in state / read X / produce Y" → **data object** with state in the ref name + data assoc.
- every XOR split → a **default flow** + conditions on the others (POSE guideline).

## POSE modeling-guideline checklist (grading)
one start event · default flow on every XOR · timer on every event-based gateway · events named
perfect-tense ("Invoice received"), activities verb-object ("Check invoice") · black-box pools +
message flow for external parties · sequence flow only inside pools, message flow only between them.

## Validate / preview commands
```bash
# bpmn-moddle (the exact importer openBPT uses) — run where bpmn-moddle is installed:
npm i bpmn-moddle && node validate.mjs path/to/file.bpmn      # want: warnings: 0

# quick visual layout check (writes layout.svg; open in a browser):
python3 preview.py path/to/file.bpmn
# rasterize to PNG if wanted:
npm i @resvg/resvg-js && node -e "const{Resvg}=require('@resvg/resvg-js'),fs=require('fs');\
fs.writeFileSync('layout.png',new Resvg(fs.readFileSync('layout.svg','utf8'),{background:'white'}).render().asPng())"
```

---

# Part 2 — Petri / workflow nets for openBPT's analysis modeler

The **Petri-net modeler** (`bptlab/openbpt-modeler-petri-net`, a diagram-js `ptn-js` app,
P/T nets only) is where you *analyse* a workflow net — soundness, reachability, token replay.
It loads TWO formats:
- **native `ptn` XML** via `importXML` — the app's own moddle format (prefix `ptn`, DI prefix
  `ptnDi`, namespaces `http://bpt-lab.org/schemas/ptn(Di)`). **This is the validated path.**
- **standard PNML 2009** via `importPNML`.

Its file picker has no extension filter, so ship both and let Fabian pick whichever his build's
menu exposes (try the `.ptn` first). Like bpmn-js, it needs full DI (a shape per node, an edge
with waypoints per arc) — the builder emits it.

### Native `ptn` format facts (from the tool's moddle schema)
- `<ptn:definitions><ptn:model><ptn:place>/<ptn:transition>/<ptn:arc>…</ptn:model>
  <ptnDi:diagram><ptnDi:plane>…</ptnDi:plane></ptnDi:diagram></ptn:definitions>`
- `name` and `initialMarking` are **child elements** (not attributes); `isSilent="true"` marks a
  **silent (black) transition** = how gateways/τ show up; arc `source`/`target` are attributes.
- DI: `<ptnDi:diagramShape modelElement=..><dc:Bounds .../></ptnDi:diagramShape>` and
  `<ptnDi:diagramEdge sourceElement=.. targetElement=..><ptnDi:waypoint xsi:type="dc:Point" .../></ptnDi:diagramEdge>`.

### Builder API (`petrinet_builder.py`)
```python
from petrinet_builder import PetriNet
pn = PetriNet("My net")
pn.place("i", "i", 80, 300, tokens=1)          # source place, initial marking 1
pn.transition("t", "Do work", 170, 300)        # labelled transition (an activity)
pn.transition("g", "", 260, 300, silent=True)  # silent transition = a gateway / τ
pn.place("o", "o", 350, 300)                    # sink place
pn.arc("i", "t"); pn.arc("t", "g"); pn.arc("g", "o")   # arc ids auto-generated
pn.save("net.ptn", "net.pnml")                  # writes both formats
```
Nodes take a **centre** `(cx, cy)`; DI bounds and 2-point arc waypoints are computed for you.

### Translating BPMN → workflow net (the mapping to emit)
- activity → labelled transition · sequence flow → place · start event → source place `i` · end
  event → sink place `o`.
- **XOR** = a place with several out-/in-transitions (choice/merge). **AND** = a silent
  transition that forks (several out-places) / syncs (several in-places). **Event-based** = XOR
  (deferred choice). **OR** = unfold into an XOR over the non-empty subsets, the multi-element
  subset being an AND block (see the example) — this keeps every join local.

### Validate / preview
```bash
npm i moddle moddle-xml && node validate_ptn.mjs net.ptn   # against openBPT's own schema; want warnings: 0
python3 petrinet_preview.py net.ptn                        # -> net.svg (rasterise like the BPMN one)
```
Schemas live in `resources/{ptn,ptnDi,dc}.json` (copied verbatim from the modeler repo).

## Worked examples
- BPMN: `examples/innovations_feature_request.py` → `innovations-feature-request.bpmn`
  (3 lanes, ad-hoc sub-process, collapsed sub-process + boundary timer, many backlog loops).
- BPMN: the apartment-application exercise (message start, two event-based gateways, inter-instance
  signal, interrupting event sub-process, data objects) — vault `bpmn-exercises/apartment-application.bpmn`.
- Petri net: `examples/or_workflow_net.py` → `or-workflow-net.ptn` + `.pnml`
  (OR gateway unfolded into XOR-over-subsets with an AND block; validated 0 warnings).

Related: [[pose]] skill for the course itself. Vault exercises live in `bpmn-exercises/`
(`.bpmn` process models, `.ptn`/`.pnml` workflow nets, `*-preview.png` previews).
