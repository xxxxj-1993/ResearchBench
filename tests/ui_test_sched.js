/* 课表导入端到端测试：粘贴 HTML → 解析 → 预览 → 导入 → 周次过滤
   注意：app.js 是 IIFE 包裹的，内部变量不暴露到 window，
   所以这里全程走真实 DOM 点击 + HTTP 断言，不碰内部状态。 */
const { spawn } = require('child_process');
const { JSDOM, VirtualConsole } = require('jsdom');
const fs = require('fs');
const pathmod = require('path');
const os = require('os');

const PY = 'C:/Users/Lenovo/.workbuddy/binaries/python/versions/3.13.12/python.exe';
const APP = pathmod.join(__dirname, '..', 'app.py');
const FIXTURE = fs.readFileSync(pathmod.join(__dirname, 'fixture_schedule.html'), 'utf-8');

const results = [];
function chk(name, cond, extra) {
  results.push(!!cond);
  console.log((cond ? 'PASS  ' : 'FAIL  ') + name + (extra !== undefined ? '   ' + extra : ''));
}
const wait = ms => new Promise(r => setTimeout(r, ms));

const ISOLATED = pathmod.join(os.tmpdir(), 'rbsched-' + Date.now());
fs.mkdirSync(ISOLATED, { recursive: true });
console.log('测试目录:', ISOLATED);

const child = spawn(PY, ['-u', APP], {
  env: Object.assign({}, process.env, { RB_NO_WINDOW: '1', APPDATA: ISOLATED }),
  stdio: ['ignore', 'pipe', 'pipe'],
});
let baseUrl = null;
child.stdout.on('data', d => {
  const m = String(d).match(/SERVING (http:\/\/127\.0\.0\.1:\d+)/);
  if (m) baseUrl = m[1];
});
child.stderr.on('data', d => process.stderr.write('[py] ' + d));

function waitFor(cond, ms = 40000) {
  return new Promise((res, rej) => {
    const t0 = Date.now();
    (function loop() {
      if (cond()) return res();
      if (Date.now() - t0 > ms) return rej(new Error('timeout'));
      setTimeout(loop, 150);
    })();
  });
}
const j = u => fetch(u).then(r => r.json());
const post = (u, body) => fetch(u, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
}).then(r => r.json());

// 本周一往前推 n 周的周一（用来控制「现在是第几周」）
function mondayOf(weeksAgo) {
  const d = new Date();
  const dow = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - dow - weeksAgo * 7);
  return d.toISOString().slice(0, 10);
}

let dom, win, doc;

async function loadPage() {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push(String((e && e.message) || e)));
  const d = await JSDOM.fromURL(baseUrl, {
    runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, baseUrl).href, o); },
  });
  dom = d; win = d.window; doc = win.document;
  await waitFor(() => doc.querySelectorAll('.nav a').length > 0, 40000);
  await wait(500);
  return errors;
}
const click = sel => { const el = doc.querySelector(sel); if (el) { el.click(); return true; } return false; };
const goto = async k => { click('[data-act="nav"][data-nav="' + k + '"]'); await wait(300); };

(async () => {
  await waitFor(() => baseUrl);
  console.log('本地服务:', baseUrl);

  // 让「现在」是第 10 周：开学日 = 9 周前的周一
  let st = await j(baseUrl + '/api/state');
  st.db.settings.semStart = mondayOf(9);
  st.db.settings.periodTimes = '08:00,08:55,10:00,10:55,14:00,14:55,16:00,16:55';
  st.db.classes = [];
  await post(baseUrl + '/api/patch', { key: 'settings', value: st.db.settings });
  await post(baseUrl + '/api/patch', { key: 'classes', value: [] });

  const errors = await loadPage();

  console.log('\n=== 课表页：周表 / 总表 ===');
  await goto('schedule');
  chk('周表视图渲染出网格', !!doc.querySelector('table.sched'));
  const tabs = [...doc.querySelectorAll('[data-act="schView"]')];
  chk('有周表/总表两个切换按钮', tabs.length === 2, tabs.map(t => t.textContent.trim()).join(' / '));

  click('[data-act="schView"][data-v="list"]');
  await wait(300);
  chk('切到总表后有导入按钮', !!doc.querySelector('[data-act="importSchedule"]'));
  chk('切到总表后周网格消失', !doc.querySelector('table.sched'));

  console.log('\n=== 导入弹窗 ===');
  click('[data-act="importSchedule"]');
  await wait(300);
  chk('弹出粘贴框', !!doc.getElementById('impPaste'));
  chk('有教师筛选输入', !!doc.getElementById('impTeacher'));
  chk('有解析按钮', !!doc.getElementById('impGo'));

  doc.getElementById('impPaste').setAttribute('data-html', FIXTURE);
  doc.getElementById('impTeacher').value = '徐';
  doc.getElementById('impGo').click();

  await waitFor(() => doc.querySelectorAll('.impck').length > 0, 40000);
  const rows = doc.querySelectorAll('.impck').length;
  chk('解析出 13 门课', rows === 13, rows + ' 门');
  const names = [...doc.querySelectorAll('.impf')].filter(e => e.getAttribute('data-k') === 'name');
  chk('课程名已填入', names.length === 13 && !!names[0].value, names[0] && names[0].value);
  const rooms = [...doc.querySelectorAll('.impf')].filter(e => e.getAttribute('data-k') === 'room');
  chk('教室 13/13 抽到', rooms.length === 13 && rooms.every(r => !!r.value),
    rooms.filter(r => !r.value).length + ' 个空');
  const cks = [...doc.querySelectorAll('.impck')];
  chk('按教师「徐」全部勾选', cks.filter(c => c.checked).length === 13,
    cks.filter(c => c.checked).length + ' 条');

  console.log('\n=== 改周次后导入 ===');
  const wkIn = [...doc.querySelectorAll('.impf')].filter(e => e.getAttribute('data-k') === 'weeks');
  wkIn.forEach(e => { e.value = '1-8'; e.dispatchEvent(new win.Event('change')); });
  await wait(150);
  click('[data-act="impDo"]');
  await wait(1500);

  st = await j(baseUrl + '/api/state');
  const cls = st.db.classes || [];
  chk('导入后课程数 = 13', cls.length === 13, cls.length + ' 条');
  chk('课程有名称', !!cls[0] && !!cls[0].name, cls[0] && cls[0].name);
  chk('课程有教室', cls.every(c => !!c.location), cls.filter(c => !c.location).length + ' 个空');
  chk('周次写成了 1-8', cls.every(c => c.weeks === '1-8'), cls[0] && cls[0].weeks);
  chk('星期都在 1-7', cls.every(c => c.day >= 1 && c.day <= 7));
  chk('节次换算正确', cls.every(c => c.sections >= 1));

  console.log('\n=== 行课周次过滤（本次修的 bug：第 10 周不该出现 1-8 周的课）===');
  click('[data-act="schView"][data-v="week"]');
  await wait(400);
  const cards10 = doc.querySelectorAll('.clscard').length;
  chk('第 10 周：1-8 周的课全部不显示', cards10 === 0, cards10 + ' 个卡片');
  const wkTag = (doc.querySelector('.pg-h .sub') || {}).textContent || '';
  chk('周表标题显示第 10 周', wkTag.indexOf('10') >= 0, wkTag.trim());

  click('[data-act="schView"][data-v="list"]');
  await wait(300);
  doc.getElementById('bulkWeeks').value = '1-20';
  click('[data-act="bulkWeeks"]');
  await wait(600);
  st = await j(baseUrl + '/api/state');
  chk('批量改周次生效', (st.db.classes || []).every(c => c.weeks === '1-20'), st.db.classes[0].weeks);

  click('[data-act="schView"][data-v="week"]');
  await wait(400);
  const cards2 = doc.querySelectorAll('.clscard').length;
  chk('改成 1-20 周后第 10 周有课了', cards2 > 0, cards2 + ' 个卡片');

  console.log('\n=== 总表行内改周次 ===');
  click('[data-act="schView"][data-v="list"]');
  await wait(300);
  const wkp = doc.querySelector('.wk-in');
  chk('总表有可编辑的周次格', !!wkp, wkp && wkp.value);
  if (wkp) {
    wkp.value = '1-12';
    // 行内编辑用的是 document 级委托，事件必须冒泡才能被接住
    wkp.dispatchEvent(new win.Event('change', { bubbles: true }));
    await wait(700);
    st = await j(baseUrl + '/api/state');
    const hit = (st.db.classes || []).filter(c => c.id === wkp.getAttribute('data-wk'))[0];
    chk('改完写回了数据', hit && hit.weeks === '1-12', hit && hit.weeks);
  }

  console.log('\n=== 错误检查 ===');
  chk('无 JS 错误', errors.length === 0, errors.slice(0, 2).join(' | '));

  const pass = results.filter(Boolean).length;
  console.log('\n结果: ' + pass + '/' + results.length + ' 通过');
  child.kill();
  await wait(500);                       // 等 python 放开 app.log
  try { fs.rmSync(ISOLATED, { recursive: true, force: true }); } catch (_) {}
  process.exit(pass === results.length ? 0 : 1);
})().catch(e => {
  console.error('测试异常:', e);
  try { child.kill(); } catch (_) {}
  process.exit(1);
});
