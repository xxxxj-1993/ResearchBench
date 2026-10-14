/* 一键更新引用：隔离数据 + 模拟 OpenAlex 响应，不访问用户数据库。 */
const { spawn } = require('child_process');
let JSDOM, VirtualConsole;
try {
  ({ JSDOM, VirtualConsole } = require('jsdom'));
} catch (error) {
  console.log('SKIP  缺少 jsdom；请先安装项目的前端测试依赖');
  process.exit(0);
}
const fs = require('fs');
const os = require('os');
const path = require('path');

const APP = path.join(__dirname, '..', 'app.py');
const TEST_HOME = fs.mkdtempSync(path.join(os.tmpdir(), 'rb-citations-'));
const child = spawn('python', ['-u', APP], {
  env: Object.assign({}, process.env, { RB_NO_WINDOW: '1', APPDATA: TEST_HOME }),
  stdio: ['ignore', 'pipe', 'pipe'],
});

let baseUrl = '';
child.stdout.on('data', d => {
  const match = String(d).match(/SERVING (http:\/\/127\.0\.0\.1:\d+)/);
  if (match) baseUrl = match[1];
});
child.stderr.on('data', d => process.stderr.write(String(d)));

function waitFor(fn, timeout = 15000) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    (function poll() {
      if (fn()) return resolve();
      if (Date.now() - started > timeout) return reject(new Error('timeout'));
      setTimeout(poll, 50);
    })();
  });
}

function request(url, options) {
  return fetch(url, options).then(r => r.json());
}

(async () => {
  try {
    await waitFor(() => baseUrl);
    const state = await request(baseUrl + '/api/state');
    state.db.pubs = [{
      id: 'cite1', title: 'Citation test', authors: 'A. Author', journal: 'Test Journal',
      year: 2026, doi: '10.1000/test', role: 'first', cites: 2,
    }];
    await request(baseUrl + '/api/state', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ db: state.db }),
    });

    const patchCalls = [];
    const errors = [];
    const vc = new VirtualConsole();
    vc.on('jsdomError', e => errors.push(String(e && e.message || e)));
    const dom = await JSDOM.fromURL(baseUrl, {
      runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
      beforeParse(window) {
        window.fetch = (input, options) => {
          const url = new URL(input, baseUrl).href;
          if (url.includes('/api/citations/refresh')) {
            return Promise.resolve({ json: () => Promise.resolve({
              ok: true,
              results: [{ id: 'cite1', cites: 18, source: 'OpenAlex',
                          openalexId: 'W18', updatedAt: '2026-09-17T12:00:00+08:00' }],
              updated: 1, skippedCount: 0, failedCount: 0,
            }) });
          }
          if (url.includes('/api/patch')) {
            const body = JSON.parse(options.body);
            patchCalls.push(body);
            return Promise.resolve({ json: () => Promise.resolve({ ok: true }) });
          }
          return fetch(url, options).then(r => ({ json: () => r.json() }));
        };
        window.navigator.sendBeacon = () => true;
      },
    });

    const doc = dom.window.document;
    await waitFor(() => doc.querySelectorAll('.nav a').length === 12);
    doc.querySelector('.nav a[data-nav="pubs"]').click();
    await waitFor(() => doc.querySelector('[data-act="refreshCites"]'));
    doc.querySelector('[data-act="refreshCites"]').click();
    await waitFor(() => patchCalls.some(x => x.key === 'pubs'));

    const saved = patchCalls.filter(x => x.key === 'pubs').pop().value[0];
    const chip = doc.querySelector('[data-act="quickCite"]');
    if (saved.cites !== 18 || saved.citesSource !== 'OpenAlex') throw new Error('引用数据未正确保存');
    if (!chip || !/引用 18 · OpenAlex/.test(chip.textContent)) throw new Error('引用卡片未正确刷新');
    if (errors.length) throw new Error(errors.join(' | '));
    console.log('PASS  一键更新引用按钮、回写、来源显示与保存');
  } finally {
    child.kill();
  }
})().catch(error => {
  console.error('FAIL ', error && error.stack || error);
  child.kill();
  process.exitCode = 1;
});
