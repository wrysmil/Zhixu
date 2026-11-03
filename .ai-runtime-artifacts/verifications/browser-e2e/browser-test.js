const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const SECRET = process.env.BOOTSTRAP_SECRET;
const BASE = process.env.BASE_URL || 'http://127.0.0.1:5173';
const OUT = process.env.OUT_DIR;
const LABEL = process.env.LABEL || 'run';

const R = { label: LABEL, base: BASE, startedAt: new Date().toISOString(), steps: [], console: [], pageErrors: [], httpFailures: [], rawI18nKeys: [] };

const log = (step, status, detail) => {
  R.steps.push({ step, status, detail: detail || '' });
  console.log(`[${status}] ${step}${detail ? ' :: ' + detail : ''}`);
};
const shot = async (page, name) => {
  const p = path.join(OUT, `${LABEL}-${name}.png`);
  await page.screenshot({ path: p, fullPage: false });
  return p;
};
const RAW_KEY = /\b(settings|app|agent|chat|eval|workspace|dock|startPanel|workbench|memory|settings)\.[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+)+/g;
const scanRawKeys = async (page, where) => {
  const txt = await page.evaluate(() => document.body.innerText || '');
  const hits = [...new Set(txt.match(RAW_KEY) || [])];
  if (hits.length) R.rawI18nKeys.push({ where, keys: hits });
  return hits;
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  page.on('console', (m) => {
    if (m.type() === 'error' || m.type() === 'warning') R.console.push({ type: m.type(), text: m.text().slice(0, 400) });
  });
  page.on('pageerror', (e) => R.pageErrors.push(String(e).slice(0, 400)));
  page.on('response', async (res) => {
    if (res.status() >= 400) {
      let body = '';
      try { body = (await res.text()).slice(0, 250); } catch {}
      R.httpFailures.push({ url: res.url().slice(0, 160), status: res.status(), body });
    }
  });

  try {
    // 1) 首页 + 自动认证
    await page.goto(`${BASE}/#/?bootstrapSecret=${encodeURIComponent(SECRET)}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForFunction(() => (document.body.innerText || '').length > 50, { timeout: 30000 });
    await page.waitForTimeout(4000);
    const homeText = await page.evaluate(() => document.body.innerText);
    R.homeText = homeText.slice(0, 1500);
    const pw = await page.locator('input[type=password]').count();
    const broken = /Couldn't reach|bootstrap failed|无法连接/i.test(homeText);
    log('首页加载 + bootstrap 自动认证', pw === 0 && !broken ? 'PASS' : 'FAIL',
      pw > 0 ? '停留在密码页' : broken ? homeText.replace(/\n+/g, ' ').slice(0, 140) : '应用外壳已渲染');
    await shot(page, '01-home');

    // 2) 评估分区
    await page.goto(`${BASE}/#/settings?section=eval&bootstrapSecret=${encodeURIComponent(SECRET)}`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(5000);
    const section = page.locator('[data-settings-section="eval"]');
    const secCount = await section.count();
    let secText = '';
    if (secCount) secText = (await section.first().innerText()).replace(/\n+/g, ' | ');
    log('进入 Settings→评估分区', secCount && secText.length > 100 ? 'PASS' : 'FAIL',
      secCount ? `渲染 ${secText.length} 字符` : '未找到 data-settings-section="eval"');
    R.evalSectionText = secText;
    await shot(page, '02-eval-overview');

    // 3) 三个 Tab（严格限定 role=tab，避免误点侧栏同名项）
    const tabs = [['overview', /^(Overview|总览|概览)$/i], ['runs', /^(Runs|运行)$/i], ['datasets', /^(Datasets|数据集)$/i]];
    for (const [key, re] of tabs) {
      const before = R.console.length;
      const clicked = await page.evaluate((src) => {
        const rx = new RegExp(src);
        const els = Array.from(document.querySelectorAll('[role=tab]'));
        const hit = els.find((el) => rx.test((el.textContent || '').trim()));
        if (!hit) return { ok: false, found: els.map((e) => (e.textContent || '').trim()) };
        hit.click();
        return { ok: true, label: (hit.textContent || '').trim() };
      }, re.source);
      await page.waitForTimeout(2800);
      const txt = await page.evaluate(() => {
        const el = document.querySelector('[data-settings-section="eval"]');
        return el ? (el.innerText || '') : '';
      });
      const errs = R.console.slice(before).filter((c) => c.type === 'error');
      const ok = clicked.ok && txt.length > 80 && errs.length === 0;
      log(`评估 Tab「${key}」`, ok ? 'PASS' : 'FAIL',
        `点击=${clicked.ok}${clicked.label ? '(' + clicked.label + ')' : ''} 内容长度=${txt.length} 新增错误=${errs.length}` +
        (errs.length ? ' :: ' + errs.map((e) => e.text).join(' | ').slice(0, 220) : ''));
      R[`tab_${key}`] = txt.replace(/\n+/g, ' | ').slice(0, 2500);
      const raw = await scanRawKeys(page, `tab:${key}`);
      if (raw.length) log(`  Tab「${key}」i18n 裸键外泄`, 'FAIL', raw.join(', '));
      await shot(page, `03-tab-${key}`);
    }

    // 4) 可靠性子区块可见性（回到 Overview Tab，那里挂着 calibration/scorecard/reviews）
    await page.evaluate(() => {
      const el = Array.from(document.querySelectorAll('[role=tab]')).find((e) => /^(Overview|总览|概览)$/i.test((e.textContent || '').trim()));
      if (el) el.click();
    });
    await page.waitForTimeout(3000);
    const fullText = await page.evaluate(() => document.body.innerText);
    const probes = [
      ['scorecard', ['Reliability Scorecard', '可靠性记分卡', 'Task Completion', '任务完成度', 'Qualification', '综合合格率']],
      ['calibration', ['Judge calibration', 'Judge 校准', 'Calibration status', '校准状态', 'Gold samples', 'gold sample']],
      ['reviews', ['Manual Review', '人工复核', 'Manual conclusion', '人工结论', 'Reviewer', '复核人']],
    ];
    const low = fullText.toLowerCase();
    for (const [key, names] of probes) {
      const hit = names.find((n) => low.includes(n.toLowerCase()));
      log(`可靠性区块「${key}」可见`, hit ? 'PASS' : 'WARN', hit || '当前视图未渲染该区块');
    }

    // 5) 评估服务 API（先登录再查；CORS 通过「fetch 未抛错且响应体可读」判定）
    const apiProbe = await page.evaluate(async () => {
      const base = `http://${window.location.hostname}:8790/api/eval/v1`;
      const out = {};
      const secret = window.localStorage.getItem('zhixu-webui.bootstrap-secret') || '';
      const raw = async (path, opts = {}) => {
        try {
          const res = await fetch(base + path, { credentials: 'include', ...opts });
          let body = '';
          try { body = (await res.text()).slice(0, 300); } catch {}
          return { status: res.status, body, corsBlocked: false };
        } catch (e) { return { status: 0, body: 'FETCH_ERROR: ' + String(e), corsBlocked: true }; }
      };
      out.login = await raw('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passphrase: secret }),
      });
      out.session = await raw('/auth/session');
      out.health = await raw('/health');
      out.profiles = await raw('/profiles');
      out.overview = await raw('/overview');
      out.suites = await raw('/suites');
      out.datasetsTemplate = await raw('/datasets/template');
      out.runs = await raw('/runs');
      out.calibrationSamples = await raw('/calibration-samples');
      out.notFound = await raw('/runs/does-not-exist');
      return out;
    });
    R.apiProbe = apiProbe;
    for (const [k, v] of Object.entries(apiProbe)) {
      const expect4xx = k === 'notFound';
      const ok = expect4xx ? (v.status === 404 || v.status === 401) : (v.status >= 200 && v.status < 400);
      const corsNote = v.corsBlocked ? ' [CORS 阻断]' : '';
      log(`评估 API ${k}`, ok ? 'PASS' : 'FAIL', `HTTP ${v.status}${corsNote} :: ${String(v.body).slice(0, 130)}`);
    }

    // 6) 交互：切到 Runs 并打开一次运行详情
    // 「Recent runs」列表在 Overview Tab，先切回 Overview 再点运行条目
    await page.evaluate(() => {
      const el = Array.from(document.querySelectorAll('[role=tab]')).find((e) => /^(Overview|总览|概览)$/i.test((e.textContent || '').trim()));
      if (el) el.click();
    });
    await page.waitForTimeout(3000);
    const detail = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      // 运行条目按钮文本形如「团队写作回归 · TeamCompleted2026/10/25 23:01:53」
      const hit = btns.find((b) => /\d{4}\/\d{1,2}\/\d{1,2}\s*\d{2}:\d{2}/.test((b.textContent || '').trim()));
      if (!hit) return { opened: false, candidates: btns.map((b) => (b.textContent || '').trim()).slice(0, 30) };
      hit.click();
      return { opened: true, label: (hit.textContent || '').trim() };
    });
    await page.waitForTimeout(3000);
    const detailText = await page.evaluate(() => document.body.innerText);
    log('运行详情抽屉可打开', detail.opened && detailText.length > 200 ? 'PASS' : 'WARN',
      detail.opened ? `点击「${detail.label}」，页面文本 ${detailText.length} 字符` : '未找到可点击的运行条目');
    // 运行详情内的可靠性三区块
    const dbody = await page.evaluate(() => document.body.innerText);
    const dlow = dbody.toLowerCase();
    for (const [key, names] of [
      ['scorecard', ['reliability scorecard', 'task completion', 'qualification rate', '可靠性记分卡', '任务完成度']],
      ['reviews', ['manual review', 'manual conclusion', 'reviewer', '人工复核', '复核人']],
    ]) {
      const hit = names.find((n) => dlow.includes(n.toLowerCase()));
      log(`运行详情内可靠性区块「${key}」`, hit ? 'PASS' : 'WARN', hit || '运行详情未渲染该区块');
    }
    const detailRaw = await scanRawKeys(page, 'run-detail');
    if (detailRaw.length) log('  运行详情 i18n 裸键外泄', 'FAIL', detailRaw.join(', '));
    await shot(page, '04-run-detail');

    // 7) 窄视口
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${BASE}/#/settings?section=eval&bootstrapSecret=${encodeURIComponent(SECRET)}`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3500);
    const ov = await page.evaluate(() => ({ s: document.documentElement.scrollWidth, c: document.documentElement.clientWidth }));
    log('窄视口(390) 无横向溢出', ov.s <= ov.c + 2 ? 'PASS' : 'WARN', `scrollWidth=${ov.s} clientWidth=${ov.c}`);
    await shot(page, '05-mobile-eval');

    const SELF_INFLICTED = [
      'http://127.0.0.1:8790/api/eval/v1/auth/session',
      'http://127.0.0.1:8790/api/eval/v1/runs/does-not-exist',
    ];
    const selfUrls = R.httpFailures.filter((h) => SELF_INFLICTED.some((u) => h.url.startsWith(u))).map((h) => h.url);
    const errCount = R.console.filter((c) => c.type === 'error').length - selfUrls.length;
    log('控制台错误汇总（已排除自查请求）', errCount === 0 && R.pageErrors.length === 0 ? 'PASS' : 'WARN',
      `consoleError=${errCount} pageError=${R.pageErrors.length} http4xx+=${R.httpFailures.length}（其中自查 ${selfUrls.length} 条）`);
    log('i18n 裸键外泄汇总', R.rawI18nKeys.length === 0 ? 'PASS' : 'FAIL',
      R.rawI18nKeys.length ? R.rawI18nKeys.map((x) => `${x.where}: ${x.keys.join(', ')}`).join(' ;; ') : '未发现裸键');
  } catch (e) {
    log('执行异常', 'FAIL', String(e).slice(0, 400));
    try { await shot(page, '99-error'); } catch {}
  }

  R.finishedAt = new Date().toISOString();
  fs.writeFileSync(path.join(OUT, `${LABEL}-report.json`), JSON.stringify(R, null, 2));
  const fails = R.steps.filter((s) => s.status === 'FAIL');
  console.log(`\n=== 汇总: ${R.steps.length} 步, FAIL=${fails.length}, WARN=${R.steps.filter((s) => s.status === 'WARN').length} ===`);
  await browser.close();
  process.exit(fails.length ? 1 : 0);
})();
