/* 最真实的复现：jsdom 加载真实 ui/index.html + 真实后端服务（临时数据目录）。
   流程：boot -> 右键改名 -> 落盘 -> 重启（新 jsdom 实例）-> 看导航是否显示自定义名。
*/
const fs = require('fs');
const path = require('path');
const http = require('http');
const { JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || 'jsdom');

const ROOT = path.join(__dirname, '..');
let APP;
try {
  APP = require('child_process'); // placeholder
} catch (e) {}

// ---- 启动真实后端（临时数据目录）----
const PY = process.env.PYTHON || 'python';
const TMP = fs.mkdtempSync(path.join(require('os').tmpdir(), 'rb_e2e_'));
const rootPY = ROOT.replace(/\\/g, '\\\\');
const tmpPY = TMP.replace(/\\/g, '\\\\');
const code = [
  "import sys, os, json, threading",
  "sys.path.insert(0, r'" + rootPY + "')",
  "import app",
  "app.DATA_DIR = r'" + tmpPY + "'",
  "app.DATA_FILE = os.path.join(app.DATA_DIR, 'data.json')",
  "app.LOG_FILE = os.path.join(app.DATA_DIR, 'app.log')",
  "app.LAUNCH = os.path.join(app.DATA_DIR, 'launch.json')",
  "app.load_db()",
  "from http.server import ThreadingHTTPServer",
  "httpd = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)",
  "port = httpd.server_address[1]",
  "print('PORT', port, flush=True)",
  "threading.Thread(target=httpd.serve_forever, daemon=True).start()",
  "import time",
  "while True: time.sleep(1)"
].join('\n');

const { spawn } = require('child_process');
const srv = spawn(PY, ['-c', code], { cwd: ROOT });
let PORT = null;
srv.stdout.on('data', d => {
  const m = String(d).match(/PORT (\d+)/);
  if (m) PORT = m[1];
  else console.log('[srv-out]', String(d).trim());
});
srv.stderr.on('data', d => console.error('[srv-err]', String(d).trim()));

const wait = ms => new Promise(r => setTimeout(r, ms));
const results = [];
function chk(name, cond, extra) {
  results.push(!!cond);
  console.log((cond ? 'PASS  ' : 'FAIL  ') + name + (extra !== undefined ? '   ' + extra : ''));
}

async function waitPort() {
  for (let i = 0; i < 50; i++) {
    if (PORT) return;
    await wait(100);
  }
  throw new Error('后端未启动');
}

function makeDom() {
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => { /* 静默资源错误 */ });
  const html = fs.readFileSync(path.join(ROOT, 'ui', 'index.html'), 'utf-8');
  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(window) {
      // 把相对路径 fetch 转发到真实后端
      window.fetch = function (url, opt) {
        const abs = String(url).startsWith('http') ? url : ('http://127.0.0.1:' + PORT + url);
        return fetch(abs, opt);
      };
      window.addEventListener('error', () => {});
    }
  });
  return dom;
}

function navText(doc) {
  return Array.from(doc.querySelectorAll('#nav a'))
    .map(a => a.textContent.replace(/\s+/g, ''));
}

(async function () {
  await waitPort();
  console.log('后端端口', PORT);

  console.log('\n=== 第一次会话：改名 ===');
  const dom1 = makeDom();
  const w1 = dom1.window, d1 = w1.document;
  await wait(600); // 等 boot + 渲染
  const labels1 = navText(d1);
  chk('首次启动导航含「课表」', labels1.join(',').indexOf('课表') >= 0, labels1.join(' / '));

  const item = d1.querySelector('#nav a[data-nav="schedule"]');
  item.dispatchEvent(new w1.MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 200 }));
  await wait(80);
  const renBtn = d1.querySelector('[data-act="navRename"]');
  chk('右键弹出「重命名」', !!renBtn);
  renBtn.dispatchEvent(new w1.MouseEvent('click', { bubbles: true, cancelable: true }));
  await wait(80);
  const inp = d1.querySelector('.lbin');
  chk('出现输入框', !!inp);
  inp.value = '研究生课表';
  inp.dispatchEvent(new w1.KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'Enter' }));
  await wait(400); // 等 fetch 真正落盘
  chk('改名后导航显示「研究生课表」', navText(d1).join(',').indexOf('研究生课表') >= 0, navText(d1).join(' / '));

  // 确认磁盘真的写了
  const disk = JSON.parse(fs.readFileSync(path.join(TMP, 'data.json'), 'utf-8'));
  chk('磁盘 data.json 已含 navLabels.schedule',
    disk.settings && disk.settings.navLabels && disk.settings.navLabels.schedule === '研究生课表',
    JSON.stringify(disk.settings && disk.settings.navLabels));

  w1.close();

  console.log('\n=== 第二次会话（模拟重启）===');
  const dom2 = makeDom();
  const w2 = dom2.window, d2 = w2.document;
  await wait(600);
  const labels2 = navText(d2);
  chk('重启后导航仍显示「研究生课表」', labels2.join(',').indexOf('研究生课表') >= 0, labels2.join(' / '));

  const bad = results.filter(x => !x).length;
  console.log('\n' + (results.length - bad) + '/' + results.length + ' passed'
    + (bad ? '  —— 有 ' + bad + ' 项失败' : ''));
  srv.kill('SIGKILL');
  process.exit(bad ? 1 : 0);
})().catch(e => {
  console.error('FATAL', e);
  try { srv.kill('SIGKILL'); } catch (_) {}
  process.exit(1);
});
