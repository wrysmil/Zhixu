---
artifact: verification-lite
route: leader-direct
tier: 1
created_at: 2026-10-22
status: passed-with-known-test-failure
skills: [imagegen, source-driven-development, verification-before-completion, agent-browser]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/.system/imagegen/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
  - C:/Users/Huangqh/.agents/skills/agent-browser/SKILL.md
source:
  - 用户要求替换左上角文字品牌和首页插画
  - 用户修正：这个换一个把，搞得可爱一些
---

# 知序可爱版品牌素材替换

## 范围与上下文

已扫描品牌、rename、welcome 相关过程产物，读取 project.profile、context-map、既有 Logo/插画验证与 2026-10-22 页面密度记录。当前技术栈沿用 React 18、Vite 5、TypeScript，不新增依赖或业务行为。

- `webui/public/brand/zhixu-owl.png`：浅蓝圆润幼年猫头鹰 Logo，256 × 256，89,504 字节。
- `webui/public/illustrations/zhixu-mascot.webp`：同一角色挥手、整理笔记的配套插画，512 × 512，36,524 字节。设置等既有复用位置同步更新。
- `webui/src/components/workspace/BrandMark.tsx`：纯文字改为共享图片，保留知序 Zhixu 替代文本、BASE_URL 前缀和调用方尺寸。
- 生图提示词：`.ai-runtime-artifacts/research/2026-10-22-zhixu-cute-brand-prompts.md`。上一版折纸角色已经被当前可爱版覆盖。

使用内置 image_gen，透明背景；只做尺寸缩放与编码压缩。TDD gate: N/A（品牌素材与图片展示替换，无新增业务逻辑）。未提交或推送。

## 命令与结果

| 检查 | 结果 |
| --- | --- |
| `tsc -p tsconfig.build.json --noEmit` | 通过 |
| 针对 BrandMark 的 ESLint | 通过 |
| `vitest run thread-shell.test.tsx workspace-navigation.test.tsx` | 96 通过，1 失败：导航测试查找 Collapse sidebar 按钮失败；没有改动导航实现或测试 |
| `vitest run thread-shell.test.tsx --no-cache` | 77/77 通过，退出码 0 |
| 最终素材写入后 `vite build --logLevel error` | 通过，退出码 0 |
| `git diff --check` | 通过（仅 LF/CRLF 提示） |
| sharp 元数据与透明度 | 两图均存在 alpha，范围 0–255 |
| public 与 nanobot/web/dist 对应资源逐字节比较 | 两图均一致 |
| 浏览器预览 | Logo 36 × 36 与插画 96 × 96 均成功加载；浅/深色目视检查无背景方块、裁切 |

首次构建遇到系统 Temp 下 esbuild 文件删除权限错误；将本次构建的 TEMP/TMP 指向 `.ai-runtime-artifacts/verifications/brand-build-temp` 后成功。第一次测试缓存写 results.json 遇 EPERM；后续使用 --no-cache 完成相关测试。保留现有 Browserslist 与 Tailwind 提示，无关联源码修改。

## 浏览器证据与边界

- `2026-10-22-cute-brand-rail-light.png`
- `2026-10-22-cute-brand-rail-dark.png`
- `2026-10-22-cute-brand-home-light.png`
- `2026-10-22-cute-brand-home-dark.png`

检查使用独立浏览器的 preview 示例数据。预览的 Creative 导航使用共享 BrandMark，首页使用共享 ThreadWelcome。没有在已认证原生 Electron 窗口中验证；普通 Sidebar 使用同一 BrandMark，源码调用尺寸为 24px。未运行全量测试。导航测试的一项失败仍保留，未将全量测试宣称为通过。

## Next

素材替换与构建已完成；刷新应用查看。没有 Git 提交或发布步骤。
