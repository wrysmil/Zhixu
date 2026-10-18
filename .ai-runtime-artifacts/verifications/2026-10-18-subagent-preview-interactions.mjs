// 仅验证设计预览的本地交互；不证明产品执行链路已实现。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const project = new URL('../../', import.meta.url);
const { Window } = await import(new URL('webui/node_modules/happy-dom/lib/index.js', project));
const fragment = readFileSync(new URL('.ai-runtime-artifacts/research/2026-10-18-subagent-observability-preview.html', project), 'utf8');
const script = fragment.match(/<script>([\s\S]*?)<\/script>/)[1];

function setup() {
  const window = new Window({ url: 'http://localhost' });
  window.matchMedia = () => ({ matches: false, addEventListener() {} });
  window.openai = {
    widgetState: {},
    setWidgetState(value) {
      this.widgetState = value;
      window.dispatchEvent(new window.CustomEvent('openai:set_globals', {
        detail: { globals: { widgetState: value } },
      }));
      return Promise.resolve();
    },
  };
  window.document.body.innerHTML = fragment.replace(/<script>[\s\S]*?<\/script>/, '');
  window.eval(script);
  return { window, document: window.document };
}

const cases = [
  ['选择另一个子任务后身份同步且菜单关闭', ({ document }) => {
    document.querySelector('[data-picker-toggle]').click();
    document.querySelector('[data-task-choice="verify"]').click();
    assert.match(document.querySelector('.zx-pane-head').textContent, /校对助手/);
    assert.equal(document.querySelector('[data-picker-toggle]').getAttribute('aria-expanded'), 'false');
  }],
  ['选择后状态回写不丢失触发器焦点', ({ document }) => {
    document.querySelector('[data-picker-toggle]').click();
    document.querySelector('[data-task-choice="verify"]').click();
    assert.ok(document.activeElement === document.querySelector('[data-picker-toggle]'), '选项确认后的焦点必须回到切换按钮');
  }],
  ['菜单外按下关闭按钮不会在click前删除按钮', ({ window, document }) => {
    document.querySelector('[data-picker-toggle]').click();
    const close = document.querySelector('[data-close]');
    close.dispatchEvent(new window.PointerEvent('pointerdown', { bubbles: true }));
    assert.equal(document.contains(close), true);
    close.click();
    assert.equal(document.querySelector('.zx-layout').dataset.surface, 'workbench');
  }],
  ['Escape只关闭菜单并保留详情与焦点', ({ window, document }) => {
    document.querySelector('[data-picker-toggle]').click();
    document.querySelector('[data-picker-toggle]').dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    assert.equal(document.querySelector('.zx-layout').dataset.surface, 'execution');
    assert.equal(document.querySelector('[data-picker-toggle]').getAttribute('aria-expanded'), 'false');
    assert.ok(document.activeElement === document.querySelector('[data-picker-toggle]'), '关闭菜单后的焦点必须回到切换按钮');
  }],
  ['关闭后重新打开保留草稿与同一任务入口', ({ document }) => {
    const draft = document.querySelector('textarea[aria-label="消息草稿"]');
    draft.value = '请补充报价日期';
    document.querySelector('[data-close]').click();
    document.querySelector('[data-open="research"]').click();
    assert.equal(draft.value, '请补充报价日期');
    assert.match(document.querySelector('.zx-pane-head').textContent, /调研助手/);
  }],
  ['文件预览返回原执行详情，不增加详情列', ({ document }) => {
    document.querySelector('[data-open-file]').click();
    assert.equal(document.querySelector('.zx-layout').dataset.surface, 'file');
    document.querySelector('[data-back]').click();
    assert.equal(document.querySelector('.zx-layout').dataset.surface, 'execution');
    assert.equal(document.querySelectorAll('.zx-pane').length, 1);
  }],
  ['方向键与Home/End可定位到对应菜单项', ({ window, document }) => {
    const key = value => document.activeElement.dispatchEvent(new window.KeyboardEvent('keydown', { key: value, bubbles: true }));
    document.querySelector('[data-picker-toggle]').focus();
    key('ArrowDown');
    assert.equal(document.activeElement.dataset.taskChoice, 'research');
    key('End');
    assert.equal(document.activeElement.dataset.taskChoice, 'verify');
    key('Home');
    assert.equal(document.activeElement.dataset.taskChoice, 'research');
  }],
];

let failures = 0;
for (const [name, run] of cases) {
  const context = setup();
  try {
    run(context);
    console.log(`PASS ${name}`);
  } catch (error) {
    failures++;
    console.error(`FAIL ${name}: ${error.message.split('\n')[0]}`);
  }
}
console.log(`${cases.length - failures}/${cases.length} preview interaction checks passed`);
// 本脚本在独立进程运行；完成检查后退出，统一回收所有离线 DOM。
process.exit(failures ? 1 : 0);
