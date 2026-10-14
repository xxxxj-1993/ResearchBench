/* 无第三方依赖：直接从正式前端源码提取周次函数，验证周表过滤结果。 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'ui', 'app.js'), 'utf8');
const start = source.indexOf('function weekExpressionIncludes(');
const end = source.indexOf('\n// 某节课在「第 wkN 周」是否要显示', start);
if (start < 0 || end < 0) throw new Error('找不到 weekExpressionIncludes 正式实现');

const context = {};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);

const cases = [
  ['10，12-14', 10, true],
  ['10，12-14', 11, false],
  ['10，12-14', 12, true],
  ['10，12-14', 14, true],
  ['10，12-14', 15, false],
  ['10,12~14', 13, true],
  ['第10、12至14周', 10, true],
  ['第10、12至14周', 11, false],
  ['1-8', 8, true],
  ['1-8', 10, false],
];

let failed = 0;
for (const [text, week, expected] of cases) {
  const actual = context.weekExpressionIncludes(text, week);
  const ok = actual === expected;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${text} · 第 ${week} 周 => ${actual}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
