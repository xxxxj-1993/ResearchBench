/* Verify the rendered common page, without loading personal data or a browser. */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync(path.join(__dirname, '..', 'ui', 'app.js'), 'utf8');
const start = source.indexOf('function viewCommon(){');
const end = source.indexOf('/* ================= 模态框架', start);
assert(start >= 0 && end > start);
const context = {
  S: {db: {materials: [], shortcuts: []}}, LANG: 'zh',
  ic: () => '', esc: s => String(s || ''), tt: s => s,
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
for (const lang of ['zh', 'en']) {
  context.LANG = lang;
  for (const materials of [[], [{id: 'test', name: 'Document', path: 'example.txt'}]]) {
    context.S.db.materials = materials;
    const html = context.viewCommon();
    assert.strictEqual((html.match(/data-act="addMaterial"/g) || []).length, 1);
    assert.strictEqual((html.match(/data-act="addMatFromZot"/g) || []).length, 1);
    assert(html.includes('class="li add" data-act="addMaterial"'));
  }
}
console.log('PASS common page retains one material add card and one Zotero button (zh/en, empty/populated)');
