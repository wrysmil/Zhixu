// WU-05 知识工作区文案键一致性校验
// 用法：node wu05-i18n-parity-check.mjs <webui 绝对路径>
// 校验：10 种语言的 knowledge.experience 键集合完全一致，且与代码实际调用点一致。
import fs from 'node:fs';
import path from 'node:path';

const webui = process.argv[2];
if (!webui) { console.error('用法: node wu05-i18n-parity-check.mjs <webui 绝对路径>'); process.exit(2); }

const LANGS = ['en', 'es', 'fr', 'id', 'ja', 'ko', 'pt-BR', 'vi', 'zh-CN', 'zh-TW'];
const namespace = 'knowledge.experience';

// 1) 代码里实际使用的键集合与中文 defaultValue
const files = [];
(function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(p);
    else if (/\.tsx?$/.test(entry.name)) files.push(p);
  }
})(path.join(webui, 'src/components/workspace'));

const code = new Map();
for (const file of files) {
  const src = fs.readFileSync(file, 'utf8');
  const re = /knowledge\.experience\.([a-zA-Z]+)["']\s*,\s*\{/g;
  let m;
  while ((m = re.exec(src))) {
    const key = m[1];
    const tail = src.slice(m.index, m.index + 400);
    const dv = tail.match(/defaultValue\s*:\s*(["'`])([^"'`]*)\1/);
    if (!code.has(key)) code.set(key, { defaultValue: dv ? dv[2] : null, files: new Set() });
    code.get(key).files.add(path.basename(file));
  }
}
const codeKeys = [...code.keys()].sort();

// 2) 各语言 JSON 的键集合
function localeKeys(lang) {
  const raw = fs.readFileSync(path.join(webui, 'src/i18n/locales', lang, 'common.json'), 'utf8');
  const data = JSON.parse(raw);
  const parts = namespace.split('.');
  let node = data;
  for (const p of parts) node = node?.[p];
  return { keys: Object.keys(node ?? {}).sort(), node };
}

const problems = [];
const rows = [];
for (const lang of LANGS) {
  let entry;
  try { entry = localeKeys(lang); }
  catch (e) { problems.push(`${lang}: JSON 解析失败 -> ${e.message}`); continue; }
  const same = JSON.stringify(entry.keys) === JSON.stringify(codeKeys);
  const empty = entry.keys.filter(k => typeof entry.node[k] !== 'string' || !entry.node[k].trim());
  if (!same) {
    const missing = codeKeys.filter(k => !entry.keys.includes(k));
    const extra = entry.keys.filter(k => !codeKeys.includes(k));
    problems.push(`${lang}: 键集合不一致 缺=[${missing}] 多=[${extra}]`);
  }
  if (empty.length) problems.push(`${lang}: 空值键 ${empty.join(',')}`);
  rows.push({ lang, keys: entry.keys.length, matchesCode: same });
}

// 3) zh-CN 必须与代码里的 defaultValue 逐字一致
const zh = localeKeys('zh-CN').node;
for (const [key, info] of code) {
  if (zh[key] !== info.defaultValue) {
    problems.push(`zh-CN.${key}: "${zh[key]}" !== 代码 defaultValue "${info.defaultValue}"`);
  }
}

// 4) 非中文语言不得与 zh-CN 完全相同（防止漏翻）
for (const lang of LANGS) {
  if (lang === 'zh-CN') continue;
  const { node } = localeKeys(lang);
  const same = codeKeys.filter(k => node[k] === zh[k]);
  if (same.length && lang !== 'zh-TW') {
    problems.push(`${lang}: 仍与 zh-CN 相同: ${same.join(',')}`);
  }
}

console.log('代码调用点键数:', codeKeys.length);
console.table(rows);
console.log('代码键清单:', codeKeys.join(','));
if (problems.length) {
  console.log('\n发现问题:');
  for (const p of problems) console.log(' -', p);
  process.exit(1);
}
console.log('\nOK: 10 种语言的 knowledge.experience 键集合与代码调用点完全一致，zh-CN 与 defaultValue 逐字一致。');
process.exit(0);