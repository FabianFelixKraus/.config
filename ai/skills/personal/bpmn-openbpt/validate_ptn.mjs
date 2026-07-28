// Validate a native `.ptn` file against openBPT's OWN moddle schemas (ptn + ptnDi + dc).
// Setup once in the folder you run from:  npm i moddle moddle-xml
// Usage:  node validate_ptn.mjs path/to/net.ptn      (want: warnings: 0)
import { Moddle } from 'moddle';
import { Reader } from 'moddle-xml';
import { readFileSync } from 'fs';

const here = (p) => new URL(p, import.meta.url);
const ptn   = JSON.parse(readFileSync(here('./resources/ptn.json')));
const ptnDi = JSON.parse(readFileSync(here('./resources/ptnDi.json')));
const dc    = JSON.parse(readFileSync(here('./resources/dc.json')));

const file = process.argv[2];
if (!file) { console.error('usage: node validate_ptn.mjs <file.ptn>'); process.exit(2); }

const moddle = new Moddle([ptn, ptnDi, dc]);
const reader = new Reader({ model: moddle, lax: true });
const rootHandler = reader.handler('ptn:Definitions');
const { rootElement, warnings } = await reader.fromXML(readFileSync(file, 'utf8'), rootHandler);

const els = rootElement.model?.modelElements || [];
const arcs = els.filter(e => e.$type === 'ptn:Arc');
const unresolved = arcs.filter(a => !a.source || !a.target).map(a => a.id);
const shapes = rootElement.diagram?.plane?.planeElements || [];
console.log('PARSE OK   root =', rootElement.$type);
console.log('  places:', els.filter(e => e.$type === 'ptn:Place').length,
            '| transitions:', els.filter(e => e.$type === 'ptn:Transition').length,
            '| arcs:', arcs.length);
console.log('  DI shapes:', shapes.filter(s => s.$type === 'ptnDi:DiagramShape').length,
            '| DI edges:', shapes.filter(s => s.$type === 'ptnDi:DiagramEdge').length);
if (unresolved.length) console.log('  UNRESOLVED arc endpoints:', unresolved.join(', '));
console.log('  warnings:', warnings.length);
warnings.forEach(w => console.log('   -', w.message));
process.exit(unresolved.length || warnings.length ? 1 : 0);
