/* 导航项「右键重命名」冒烟测试：
   1) 课表页的导航名不再是「今日安排」
   2) 右键导航项弹出菜单
   3) 就地改名 → 写库 → 导航与页标题同步
   4) 恢复默认名
   用法：node --experimental-vm-modules 无需，直接 node tests/ui_test_navrename.js
*/
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || 'jsdom');

const HTML = path.join(__dirname, '..', 'ui', 'index.html');

const results = [];
function chk(name, cond, extra) {
  results.push(!!cond);
  console.log((cond ? 'PASS  ' : 'FAIL  ') + name + (extra !== undefined ? '   ' + extra : ''));
}
const wait = ms => new Promise(r => setTimeout(r, ms));

function seedDb() {
  return {
    settings: { lang: 'zh', me: '', pin: '', focusMin: 25, zoteroDir: '',
                obsidianVault: '', obsidianIdeaFolder: '灵感', semStart: '',
                periodTimes: '', navLabels: {} },
    papers: [], projects: [], pubs: [], grants: [], teaching: [], teachProjects: [],
    patents: [], plans: [], materials: [], ideas: [], diary: [], events: [],
    shortcuts: [], focus: { done: 0, minutes: 0, date: '2026-09-16' }, classes: []
  };
}

(async function () {
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => console.error('[jsdom]', e.message));
  const patches = [];

  const dom = new JSDOM(fs.readFileSync(HTML, 'utf-8'), {
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(window) {
      window.fetch = function (url, opt) {
        if (String(url).indexOf('/api/state') >= 0) {
          return Promise.resolve({ json: () => Promise.resolve(
            { ok: true, db: seedDb(), version: '2.4.2', dataDir: 'X:\\tmp' }) });
        }
        if (String(url).indexOf('/api/patch') >= 0) {
          const body = JSON.parse(opt.body);
          patches.push(body);
          return Promise.resolve({ json: () => Promise.resolve({ ok: true }) });
        }
        return Promise.resolve({ json: () => Promise.resolve({ ok: true, vaults: [] }) });
      };
    }
  });
  const { window } = dom;
  const doc = window.document;
  await wait(300);

  const navText = () => Array.from(doc.querySelectorAll('#nav a'))
    .map(a => a.textContent.replace(/\s+/g, ''));
  const navItem = k => doc.querySelector('#nav a[data-nav="' + k + '"]');

  console.log('\n=== 1. 导航默认名 ===');
  const labels = navText();
  chk('导航出现「课表」', labels.join(',').indexOf('课表') >= 0, labels.join(' / '));
  chk('导航不再出现「今日安排」', labels.join(',').indexOf('今日安排') < 0);
  chk('「今日」页卡片仍是「今日安排」', doc.body.innerHTML.indexOf('今日安排') >= 0);

  console.log('\n=== 2. 右键菜单 ===');
  const item = navItem('schedule');
  chk('找到 schedule 导航项', !!item);
  item.dispatchEvent(new window.MouseEvent('contextmenu',
    { bubbles: true, cancelable: true, clientX: 40, clientY: 200 }));
  await wait(60);
  const ctx = doc.querySelector('.ctx');
  chk('右键弹出菜单', !!ctx);
  chk('菜单含「重命名」', !!doc.querySelector('[data-act="navRename"]'));
  chk('未改名时不显示「恢复默认名称」', !doc.querySelector('[data-act="navReset"]'));

  console.log('\n=== 3. 就地改名 ===');
  doc.querySelector('[data-act="navRename"]').dispatchEvent(
    new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  await wait(60);
  const inp = doc.querySelector('.lbin');
  chk('出现就地输入框', !!inp);
  chk('输入框占位为默认名「课表」', inp && inp.getAttribute('placeholder') === '课表',
    inp && inp.getAttribute('placeholder'));
  inp.value = '研究生课表';
  inp.dispatchEvent(new window.KeyboardEvent('keydown',
    { bubbles: true, cancelable: true, key: 'Enter' }));
  await wait(120);
  chk('导航已改成「研究生课表」',
    navText().join(',').indexOf('研究生课表') >= 0, navText().join(' / '));
  chk('改名后菜单已关闭', !doc.querySelector('.ctx'));
  const p = patches.filter(x => x.key === 'settings').pop();
  chk('已写入 settings.navLabels',
    !!p && p.value && p.value.navLabels && p.value.navLabels.schedule === '研究生课表',
    JSON.stringify(p && p.value && p.value.navLabels));

  console.log('\n=== 4. 页标题同步 + 恢复默认 ===');
  navItem('schedule').dispatchEvent(new window.MouseEvent('click',
    { bubbles: true, cancelable: true }));
  await wait(120);
  chk('课表页 H1 用了自定义名', doc.body.innerHTML.indexOf('研究生课表') >= 0);

  navItem('schedule').dispatchEvent(new window.MouseEvent('contextmenu',
    { bubbles: true, cancelable: true, clientX: 40, clientY: 200 }));
  await wait(60);
  chk('改名后菜单出现「恢复默认名称」', !!doc.querySelector('[data-act="navReset"]'));
  doc.querySelector('[data-act="navReset"]').dispatchEvent(
    new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  await wait(120);
  chk('已恢复为「课表」',
    navText().join(',').indexOf('课表') >= 0 && navText().join(',').indexOf('研究生课表') < 0,
    navText().join(' / '));

  console.log('\n=== 5. 改别的导航项也生效 ===');
  navItem('papers').dispatchEvent(new window.MouseEvent('contextmenu',
    { bubbles: true, cancelable: true, clientX: 40, clientY: 240 }));
  await wait(60);
  doc.querySelector('[data-act="navRename"]').dispatchEvent(
    new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  await wait(60);
  const inp2 = doc.querySelector('.lbin');
  inp2.value = '论文在写';
  inp2.dispatchEvent(new window.KeyboardEvent('keydown',
    { bubbles: true, cancelable: true, key: 'Enter' }));
  await wait(120);
  chk('「论文」改名为「论文在写」', navText().join(',').indexOf('论文在写') >= 0,
    navText().join(' / '));

  const bad = results.filter(x => !x).length;
  console.log('\n' + (results.length - bad) + '/' + results.length + ' passed'
    + (bad ? '  —— 有 ' + bad + ' 项失败' : ''));
  window.close();
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
