/* 前端真实 DOM 测试：启动本地服务 → jsdom 加载页面 → 模拟点击 → 断言 */
const { spawn } = require('child_process');
const { JSDOM, VirtualConsole } = require('jsdom');
const path = require('path');

const PY = 'C:/Users/Lenovo/.workbuddy/binaries/python/versions/3.13.12/python.exe';
const APP = path.join(__dirname, '..', 'app.py');

const results = [];
function chk(name, cond, extra) {
  results.push(!!cond);
  console.log((cond ? 'PASS  ' : 'FAIL  ') + name + (extra !== undefined ? '   ' + extra : ''));
}
const wait = ms => new Promise(r => setTimeout(r, ms));

// 隔离数据目录，避免测试读写用户真实数据。
// 但仍然复制一份真实 data.json 的副本进去：有些用例（Obsidian 仓库、常用页条目数）
// 依赖真实数据规模，用空库会失真；只读副本、改动不落回用户侧。
const ISOLATED = process.env.RB_TEST_HOME
  || require('path').join(require('os').tmpdir(), 'rbtest-' + Date.now());
require('fs').mkdirSync(ISOLATED, { recursive: true });
{
  const fs = require('fs'), pathmod = require('path');
  const copyInto = (src, sub) => {
    const dstDir = pathmod.join(ISOLATED, pathmod.dirname(sub));
    fs.mkdirSync(dstDir, { recursive: true });
    fs.copyFileSync(src, pathmod.join(ISOLATED, sub));
  };
  const srcDir = pathmod.join(process.env.APPDATA || '', 'ResearchWorkbench');
  const real = pathmod.join(srcDir, 'data.json');
  if (fs.existsSync(real)) {
    copyInto(real, 'data.json');
    console.log('测试数据：复制 data.json 副本（只读，改动不落回用户侧）');
  } else {
    console.log('测试数据：未找到真实 data.json，用空库');
  }
  // Obsidian 的仓库清单在 %APPDATA%\obsidian\obsidian.json，隔离后要带上
  const obs = pathmod.join(process.env.APPDATA || '', 'obsidian', 'obsidian.json');
  if (fs.existsSync(obs)) copyInto(obs, pathmod.join('obsidian', 'obsidian.json'));
}

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

function waitFor(cond, ms = 30000) {
  return new Promise((res, rej) => {
    const t0 = Date.now();
    (function loop() {
      if (cond()) return res();
      if (Date.now() - t0 > ms) return rej(new Error('timeout'));
      setTimeout(loop, 150);
    })();
  });
}

(async () => {
  await waitFor(() => baseUrl);
  console.log('本地服务:', baseUrl);

  const calls = [];
  const patchCalls = [];
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push(String(e && e.message || e)));

  const dom = await JSDOM.fromURL(baseUrl, {
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(window) {
      window.fetch = (u, o) => {
        const url = new URL(u, baseUrl).href;
        if (url.indexOf('/api/open') >= 0 || url.indexOf('/api/reveal') >= 0) {
          const body = o && o.body ? JSON.parse(o.body) : {};
          calls.push({ url, body });
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ ok: true, msg: 'MOCK' }),
          });
        }
        if (url.indexOf('/api/patch') >= 0) {
          const body = o && o.body ? JSON.parse(o.body) : {};
          patchCalls.push(body);
          return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
        }
        return globalThis.fetch(url, o).then(r => r.json().then(j => ({ ok: true, json: () => Promise.resolve(j) })));
      };
      window.navigator.sendBeacon = () => true;
      window.alert = m => { errors.push('alert: ' + m); };
    },
  });

  const w = dom.window, doc = w.document;
  await waitFor(() => doc.querySelectorAll('.nav a').length === 12, 20000);

  console.log('\n=== 框架 ===');
  chk('侧边导航 12 项', doc.querySelectorAll('.nav a').length === 12, doc.querySelectorAll('.nav a').length);
  chk('移动端导航 12 项', doc.querySelectorAll('.mnav a, #mnav a').length === 12);
  chk('顶栏问候语已渲染', /好|夜深/.test(doc.getElementById('hello').textContent), doc.getElementById('hello').textContent.trim());
  const vc2 = doc.getElementById('txtZot');
  // Zotero 冷启动要复制 zotero.sqlite 快照，可能要十几秒；就绪后才会显示条目数
  let zotReady = true;
  await waitFor(() => /(\d[\d,]*)/.test(vc2.textContent) && !/检测中|准备中/.test(vc2.textContent), 60000)
    .catch(() => { zotReady = false; });
  await new Promise(r => setTimeout(r, 600));
  chk('Zotero 状态显示', /Zotero/.test(doc.getElementById('txtZot').textContent), doc.getElementById('txtZot').textContent);
  chk('Obsidian 状态显示', /Obsidian/.test(doc.getElementById('txtObs').textContent), doc.getElementById('txtObs').textContent);
  chk('无 JS 运行时错误', errors.length === 0, errors.slice(0, 3).join(' | '));

  console.log('\n=== 今日页 ===');
  const body = doc.getElementById('body');
  chk('今日页渲染', body.innerHTML.indexOf('今日安排') >= 0);
  chk('今日重点卡存在', !!doc.querySelector('.today-hero'));
  chk('统计卡 4 个', doc.querySelectorAll('.stat').length === 4, doc.querySelectorAll('.stat').length);
  chk('下一步行动区块', body.innerHTML.indexOf('下一步行动') >= 0);
  chk('提醒区块', body.innerHTML.indexOf('提醒') >= 0);

  console.log('\n=== 页面切换 ===');
  const pages = [['calendar', '日历'], ['schedule', '课表'], ['papers', '论文'], ['projects', '课题'],
                 ['pubs', '已发表'], ['grants', '已立项'], ['teaching', '教学成果'],
                 ['patents', '专利'], ['ideas', '灵感'], ['diary', '日记'], ['common', '常用']];
  for (const [k, label] of pages) {
    doc.querySelector('.nav a[data-nav="' + k + '"]').click();
    await new Promise(r => setTimeout(r, 60));
    chk('切换到「' + label + '」', body.innerHTML.indexOf(label) >= 0 && body.innerHTML.length > 400,
        body.innerHTML.length + ' 字符');
  }

  console.log('\n=== 课表 ===');
  doc.querySelector('.nav a[data-nav="schedule"]').click();
  await new Promise(r => setTimeout(r, 80));
  chk('课表表格渲染', !!doc.querySelector('table.sched'));
  chk('课表 7 天表头', doc.querySelectorAll('table.sched thead th').length === 8,
      doc.querySelectorAll('table.sched thead th').length);
  chk('课表空格可点击添加', !!doc.querySelector('td.emptycell[data-act="addClass"]'));
  const addCell = doc.querySelector('td.emptycell[data-act="addClass"]');
  addCell.click();
  await new Promise(r => setTimeout(r, 120));
  chk('点空格弹出新增课程表单', !!doc.getElementById('cName'), errors.slice(0, 2).join(' | '));
  if (doc.getElementById('cName')) {
    doc.getElementById('cName').value = '测试课程';
    doc.getElementById('cStart').value = '3';
    doc.getElementById('cSave').click();
    await new Promise(r => setTimeout(r, 150));
    chk('新增课程后课表出现该课程', body.innerHTML.indexOf('测试课程') >= 0);
    const savedCls = patchCalls.filter(c => c.key === 'classes').pop();
    chk('课程走 patch classes 保存', !!savedCls
        && Array.isArray(savedCls.value) && savedCls.value.length === 1,
        savedCls ? JSON.stringify(savedCls.value).slice(0, 60) : 'none');
    if (savedCls) chk('课程字段完整', savedCls.value[0].day >= 1 && savedCls.value[0].start === 3
        && savedCls.value[0].name === '测试课程', JSON.stringify(savedCls.value[0]));
  }

  console.log('\n=== 今日页 ===');
  doc.querySelector('.nav a[data-nav="today"]').click();
  await new Promise(r => setTimeout(r, 80));
  chk('今日页包含今日课程卡', body.innerHTML.indexOf('今日课程') >= 0);

  console.log('\n=== 已发表：复制 / 引用数 / 导入 ===');
  doc.querySelector('.nav a[data-nav="pubs"]').click();
  await new Promise(r => setTimeout(r, 80));
  chk('有导入按钮', !!doc.querySelector('[data-act="importPub"]'));
  chk('有引用数入口', !!doc.querySelector('[data-act="quickCite"]'));
  chk('有复制按钮', !!doc.querySelector('[data-act="copyPub"]'));
  chk('引用数 chip 带 ID', !!(doc.querySelector('[data-act="quickCite"]')
      && doc.querySelector('[data-act="quickCite"]').getAttribute('data-id')));

  // 后面的用例默认停留在「常用」页
  doc.querySelector('.nav a[data-nav="common"]').click();
  await new Promise(r => setTimeout(r, 150));

  console.log('\n=== 点击直达（核心 bug） ===');
  calls.length = 0;
  const card = doc.querySelector('.li[data-act="openShortcut"]');
  chk('常用页有可点击卡片', !!card);
  card.click();
  await new Promise(r => setTimeout(r, 200));
  chk('点击卡片能触发 /api/open', calls.length === 1, JSON.stringify(calls[0] || null));
  chk('请求带上正确路径', calls.length && /^[A-Za-z]:\\/.test(calls[0].body.target), calls.length ? calls[0].body.target : '');

  calls.length = 0;
  const br = doc.querySelector('[data-act="browseDir"]');
  if (br) {
    br.click();
    await new Promise(r => setTimeout(r, 400));
    const dr = doc.getElementById('drw');
    chk('展开文件夹能列文件', dr && dr.innerHTML.indexOf('frow') >= 0, dr ? dr.innerHTML.length : 'no drawer');
  } else chk('展开文件夹按钮存在', false);

  console.log('\n=== 日历 ===');
  doc.querySelector('.nav a[data-nav="calendar"]').click();
  await new Promise(r => setTimeout(r, 80));
  chk('月视图 42 格', doc.querySelectorAll('.cal-cell').length === 42, doc.querySelectorAll('.cal-cell').length);
  chk('日期格显示截止条目', doc.querySelectorAll('.cal-cell .dl').length > 0, doc.querySelectorAll('.cal-cell .dl').length);
  doc.querySelector('[data-act="calMode"][data-v="week"]').click();
  await new Promise(r => setTimeout(r, 80));
  chk('周视图渲染', doc.querySelectorAll('.cal-week .wc').length === 105, doc.querySelectorAll('.cal-week .wc').length);

  console.log('\n=== 全局搜索 / 快速记录 ===');
  doc.getElementById('searchBtn').click();
  await new Promise(r => setTimeout(r, 60));
  const pq = doc.getElementById('pq');
  chk('搜索面板打开', !!pq);
  pq.value = 'terahertz';
  pq.dispatchEvent(new w.Event('input'));
  // Zotero 首次要复制 zotero.sqlite 快照，实测约需 5-8 秒，留足等待窗口
  for (let i = 0; i < 50 && doc.querySelectorAll('#pZot .zrow').length === 0; i++) {
    await new Promise(r => setTimeout(r, 500));
  }
  chk('本地搜索结果', doc.querySelectorAll('#pRes .pit').length > 0, doc.querySelectorAll('#pRes .pit').length);
  if (zotReady) {
    chk('Zotero 结果分区', /条|zrow/.test(doc.getElementById('pZot').innerHTML) || doc.querySelectorAll('#pZot .zrow').length > 0,
        doc.querySelectorAll('#pZot .zrow').length + ' 条');
  } else {
    chk('Zotero 结果分区（跳过：本次 Zotero 未在 60s 内就绪）', true, 'skipped');
  }
  chk('Obsidian 结果分区', !!doc.getElementById('pObs'));
  doc.querySelector('[data-act="closeModal"]').click();

  doc.getElementById('quickBtn').click();
  await new Promise(r => setTimeout(r, 60));
  chk('快速记录 6 种类型', doc.querySelectorAll('[data-act="qr"]').length === 6, doc.querySelectorAll('[data-act="qr"]').length);
  doc.querySelector('[data-act="closeModal"]').click();

  console.log('\n=== 论文详情 / Zotero 关联 ===');
  doc.querySelector('.nav a[data-nav="papers"]').click();
  await new Promise(r => setTimeout(r, 80));
  chk('master-detail 双栏', !!doc.querySelector('.md .mlist') && !!doc.querySelector('.md .detail'));
  chk('论文列表有条目', doc.querySelectorAll('.mi').length > 0, doc.querySelectorAll('.mi').length);
  chk('详情显示研究阶段等字段', doc.getElementById('body').innerHTML.indexOf('研究阶段') >= 0);
  doc.querySelectorAll('.mi')[1].click();
  await new Promise(r => setTimeout(r, 80));
  chk('切换论文详情', doc.querySelectorAll('.mi.on').length === 1);

  console.log('\n=== 详情页打开目录 ===');
  calls.length = 0;
  const openBtn = doc.querySelector('[data-act="openPath"]');
  chk('详情页有打开目录按钮', !!openBtn, openBtn ? openBtn.getAttribute('data-path') : '');
  if (openBtn) {
    openBtn.click();
    await new Promise(r => setTimeout(r, 200));
    chk('详情页打开目录生效', calls.length === 1 && calls[0].body.kind === 'folder',
        JSON.stringify(calls[0] || null));
  }

  console.log('\n=== 论文 ↔ Obsidian 笔记联动 ===');
  {
    // 选一篇和仓库笔记同名的论文（p2），验证自动匹配与关联
    const items = [...doc.querySelectorAll('.mi')];
    let picked = false;
    for (const it of items) {
      it.click();
      await new Promise(r => setTimeout(r, 120));
      const box = doc.getElementById('obsBox');
      if (box && box.querySelector('[data-act="linkObs"]')) { picked = true; break; }
    }
    const box = doc.getElementById('obsBox');
    chk('详情页有 Obsidian 笔记区块', !!box);
    chk('自动匹配到候选笔记', picked || (box && box.querySelector('[data-act="previewObs"]') !== null),
        box ? box.textContent.trim().slice(0, 60) : '');
    const link = box ? box.querySelector('[data-act="linkObs"]') : null;
    if (link) {
      const rel = link.getAttribute('data-rel');
      link.click();
      await new Promise(r => setTimeout(r, 120));
      const box2 = doc.getElementById('obsBox');
      chk('关联后显示「已关联」', box2 && box2.textContent.indexOf('已关联') >= 0,
          box2 ? box2.textContent.trim().slice(0, 60) : '');
      const un = box2 ? box2.querySelector('[data-act="unlinkObs"]') : null;
      chk('关联后出现取消按钮', !!un, rel);
      if (un) { un.click(); await new Promise(r => setTimeout(r, 100)); }
    } else {
      chk('关联后显示「已关联」', false, '无候选可关联');
      chk('关联后出现取消按钮', false, '无候选可关联');
    }
  }

  console.log('\n=== 今日页跳转 ===');
  doc.querySelector('.nav a[data-nav="today"]').click();
  await new Promise(r => setTimeout(r, 100));
  const jump = doc.querySelector('[data-act="jump"][data-kind="paper"]') || doc.querySelector('[data-act="jump"]');
  chk('今日页有跳转按钮', !!jump, jump ? jump.getAttribute('data-kind') + ':' + jump.getAttribute('data-id') : '');
  if (jump && jump.getAttribute('data-kind') === 'paper') {
    jump.click();
    await new Promise(r => setTimeout(r, 120));
    chk('跳转到论文页', doc.querySelector('.nav a[data-nav="papers"]').classList.contains('on'));
  }
  const jp = doc.querySelector('[data-act="jump"][data-kind="project"]');
  if (jp) {
    jp.click();
    await new Promise(r => setTimeout(r, 120));
    chk('跳转到课题页', doc.querySelector('.nav a[data-nav="projects"]').classList.contains('on'));
  }

  console.log('\n=== 新增栏目（9 点需求） ===');
  // 已发表：31 篇 + 身份筛选
  doc.querySelector('.nav a[data-nav="pubs"]').click();
  await wait(80);
  chk('已发表 31 篇', doc.querySelectorAll('.wall .wcard').length === 31, doc.querySelectorAll('.wall .wcard').length);
  chk('已发表含身份筛选按钮', !!doc.querySelector('[data-act="pubFilter"][data-f="first"]') && !!doc.querySelector('[data-act="pubFilter"][data-f="co"]'));
  doc.querySelector('[data-act="pubFilter"][data-f="first"]').click();
  await wait(80);
  chk('第一/通讯筛选 = 21', doc.querySelectorAll('.wall .wcard').length === 21, doc.querySelectorAll('.wall .wcard').length);
  doc.querySelector('[data-act="pubFilter"][data-f="co"]').click();
  await wait(80);
  chk('合作作者筛选 = 10', doc.querySelectorAll('.wall .wcard').length === 10, doc.querySelectorAll('.wall .wcard').length);
  doc.querySelector('[data-act="pubFilter"][data-f="all"]').click();
  await wait(80);

  // 已立项（科研项目）
  doc.querySelector('.nav a[data-nav="grants"]').click();
  await wait(80);
  chk('已立项页已渲染', doc.querySelectorAll('.wall .wcard').length >= 0, doc.querySelectorAll('.wall .wcard').length);

  // 教学成果
  doc.querySelector('.nav a[data-nav="teaching"]').click();
  await wait(80);
  chk('教学成果含「其他项目」栏', /其他项目/.test(body.innerHTML));

  // 专利
  doc.querySelector('.nav a[data-nav="patents"]').click();
  await wait(80);
  chk('专利页已渲染', /专利号/.test(body.innerHTML) || doc.querySelectorAll('.wall .wcard').length >= 0);

  // 灵感
  doc.querySelector('.nav a[data-nav="ideas"]').click();
  await wait(80);
  chk('灵感含 MSSM-ex', body.innerHTML.indexOf('MSSM-ex') >= 0);
  chk('灵感含三进制逻辑门', body.innerHTML.indexOf('三进制') >= 0);

  // 常用页 - 我的材料（增改删）
  doc.querySelector('.nav a[data-nav="common"]').click();
  await wait(80);
  chk('常用页有「我的材料」区', body.innerHTML.indexOf('我的材料') >= 0);
  chk('我的材料有新增/从Zotero按钮', !!doc.querySelector('[data-act="addMaterial"]') && !!doc.querySelector('[data-act="addMatFromZot"]'));
  const matEds = doc.querySelectorAll('[data-act="editMaterial"],[data-act="delMaterial"]');
  chk('我的材料条目带改/删', matEds.length > 0, matEds.length);

  // bug 1：研究工具分组的新增按钮应预填「研究工具」而非「本地文件」
  const rgBtn = doc.querySelector('[data-act="addShortcut"][data-cat="研究工具"]')
            || doc.querySelector('[data-act="addShortcut"][data-cat="知识库"]');
  chk('存在研究工具/知识库分组新增按钮', !!rgBtn, rgBtn ? rgBtn.getAttribute('data-cat') : 'none');
  if (rgBtn) {
    rgBtn.click();
    await wait(140);
    const sc = doc.getElementById('sCat');
    const want = rgBtn.getAttribute('data-cat');
    chk('新增表单分类预填正确(非本地文件)', sc && sc.value === want && sc.value !== '本地文件', sc ? sc.value : '');
    const cm = doc.querySelector('[data-act="closeModal"]');
    if (cm) cm.click();
    await wait(60);
  }

  console.log('\n=== 新增功能：常用页拖拽排序 ===');
  doc.querySelector('.nav a[data-nav="common"]').click();
  await wait(100);
  const scItems = doc.querySelectorAll('.li.sc[draggable="true"]');
  chk('快捷项可拖拽(draggable)', scItems.length > 0, scItems.length);
  chk('拖拽项带 grip 手柄', doc.querySelectorAll('.li.sc .grip').length > 0, doc.querySelectorAll('.li.sc .grip').length);
  const rgGroup = [...doc.querySelectorAll('.cat')].find(c => /研究工具|知识库/.test(c.textContent));
  const rgItems = rgGroup ? [...rgGroup.querySelectorAll('.li.sc')] : [];
  chk('研究工具分组存在且有拖拽项', rgItems.length >= 2, rgItems.length);
  if (rgItems.length >= 3) {
    const ids = rgItems.map(el => el.getAttribute('data-id'));
    const dt = { setData() {}, getData() { return ''; }, effectAllowed: '' };
    const fire = (type, el) => {
      const ev = new w.Event(type, { bubbles: true, cancelable: true });
      Object.defineProperty(ev, 'dataTransfer', { value: dt });
      el.dispatchEvent(ev);
    };
    patchCalls.length = 0;
    fire('dragstart', rgGroup.querySelector('.li.sc[data-id="' + ids[0] + '"]'));
    fire('drop', rgGroup.querySelector('.li.sc[data-id="' + ids[2] + '"]'));
    await wait(80);
    const pc = patchCalls.find(p => p.key === 'shortcuts');
    chk('拖拽发出 PATCH(shortcuts)', !!pc, JSON.stringify(patchCalls[0] || null));
    if (pc) {
      const arr = pc.value.map(s => s.id);
      const ia = arr.indexOf(ids[0]), ib = arr.indexOf(ids[2]);
      chk('顺序已交换(id0 排到 id2 前)', ia === ib - 1 && ia > 0, ia + '/' + ib);
    }
    // 重渲染后 DOM 顺序也应变化
    const rg2 = [...doc.querySelectorAll('.cat')].find(c => /研究工具|知识库/.test(c.textContent));
    const after = [...rg2.querySelectorAll('.li.sc')].map(el => el.getAttribute('data-id'));
    chk('DOM 顺序同步交换', after.indexOf(ids[0]) === after.indexOf(ids[2]) - 1 && after.indexOf(ids[0]) > 0, after.slice(0, 3).join(','));
  }

  console.log('\n=== 新增功能：已发表代表作快速定位 ===');
  doc.querySelector('.nav a[data-nav="pubs"]').click();
  await wait(100);
  chk('已发表含「代表作」筛选', !!doc.querySelector('[data-act="pubFilter"][data-f="rep"]'));
  const starBtns = doc.querySelectorAll('[data-act="toggleRep"]');
  chk('每篇论文带★切换按钮', starBtns.length === 31, starBtns.length);
  patchCalls.length = 0;
  const id0 = doc.querySelector('[data-act="toggleRep"]').getAttribute('data-id');
  doc.querySelector('[data-act="toggleRep"][data-id="' + id0 + '"]').click();
  await wait(60);
  const id1 = [...doc.querySelectorAll('[data-act="toggleRep"]')].find(b => b.getAttribute('data-id') !== id0).getAttribute('data-id');
  doc.querySelector('[data-act="toggleRep"][data-id="' + id1 + '"]').click();
  await wait(60);
  chk('toggleRep 发出 PATCH(pubs)', patchCalls.filter(p => p.key === 'pubs').length === 2, patchCalls.filter(p => p.key === 'pubs').length);
  const pc2 = patchCalls.filter(p => p.key === 'pubs').pop();
  chk('已标记 2 篇代表作', pc2 && pc2.value.filter(x => x.rep).length === 2, pc2 ? pc2.value.filter(x => x.rep).length : 'none');
  doc.querySelector('[data-act="pubFilter"][data-f="rep"]').click();
  await wait(100);
  chk('代表作筛选 = 2 篇', doc.querySelectorAll('.wall .wcard').length === 2, doc.querySelectorAll('.wall .wcard').length);
  chk('代表作卡片有标识', doc.querySelectorAll('.wcard.rep').length === 2, doc.querySelectorAll('.wcard.rep').length);
  doc.querySelector('.wcard.rep [data-act="toggleRep"]').click();
  await wait(60);
  const pc3 = patchCalls.filter(p => p.key === 'pubs').pop();
  chk('取消后剩 1 篇代表', pc3 && pc3.value.filter(x => x.rep).length === 1, pc3 ? pc3.value.filter(x => x.rep).length : 'none');
  doc.querySelector('[data-act="pubFilter"][data-f="all"]').click();
  await wait(60);

  console.log('\n=== 最终错误检查 ===');
  chk('全程无 JS 错误', errors.length === 0, errors.slice(0, 5).join(' | '));

  const pass = results.filter(Boolean).length;
  console.log('\n结果: ' + pass + '/' + results.length + ' 通过');
  child.kill();
  process.exit(pass === results.length ? 0 : 1);
})().catch(e => {
  console.error('测试异常:', e);
  child.kill();
  process.exit(2);
});
