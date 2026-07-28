// Validate a .bpmn file the way app.openbpt.org (bpmn-js) imports it.
// Usage:  npm i bpmn-moddle   &&   node validate.mjs path/to/file.bpmn
import BpmnModdle from 'bpmn-moddle';
import { readFileSync } from 'fs';

const file = process.argv[2];
if (!file) { console.error('usage: node validate.mjs <file.bpmn>'); process.exit(2); }
const moddle = new BpmnModdle();
const xml = readFileSync(file, 'utf8');
try {
  const { rootElement, warnings } = await moddle.fromXML(xml);
  console.log('PARSE OK  root =', rootElement.$type);
  console.log('warnings :', warnings.length);
  for (const w of warnings) console.log('  -', w.message);
  const { xml: out } = await moddle.toXML(rootElement, { format: false });
  console.log('re-serialize OK, length', out.length);
  process.exit(warnings.length ? 1 : 0);
} catch (e) {
  console.error('PARSE ERROR:', e.message);
  process.exit(1);
}
