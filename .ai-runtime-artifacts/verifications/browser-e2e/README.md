# 浏览器 E2E（评估 UI）复现说明

## 前置

```bash
# 1) gateway（WebUI :8765 + 评估服务 :8790 + health :18790）
.venv/bin/nanobot gateway

# 2) WebUI dev server（:5173，代理 /webui /api /auth 到 :8765）
cd webui && ./node_modules/.bin/vite --port 5173 --strictPort
```

## 依赖

Playwright 只作为**验证工具**，不写入仓库依赖：

```bash
mkdir -p /tmp/zhixu-pw && cd /tmp/zhixu-pw && npm init -y && npm i playwright@latest
# 用本机已装 Chrome（channel=chrome），无需下载 chromium
```

## 运行

```bash
cd <repo>
SECRET=$(.venv/bin/python -c "import json,os;print(json.load(open(os.path.expanduser('~/.nanobot/config.json')))['channels']['websocket']['tokenIssueSecret'])")
NODE_PATH=/tmp/zhixu-pw/node_modules \
BOOTSTRAP_SECRET="$SECRET" \
OUT_DIR=".ai-runtime-artifacts/verifications/browser-e2e" \
LABEL="final" \
node .ai-runtime-artifacts/verifications/browser-e2e/browser-test.js
```

退出码：0 = 无 FAIL。报告落 `browser-e2e/<LABEL>-report.json`，截图落 `browser-e2e/<LABEL>-*.png`。

## 覆盖范围

1. 首页加载 + bootstrap 自动认证（`#/?bootstrapSecret=`，无密码页）
2. Settings → Evaluation 分区渲染
3. 评估 Tab：Overview / Runs / Datasets 切换 + console 错误
4. 运行详情抽屉打开 + 可靠性记分卡 / 人工复核区块
5. 评估服务 API（`/api/eval/v1` 前缀）：login / session / health / profiles / overview / suites / runs / calibration-samples / datasets-template
6. 错误路径：404 契约体（`RECORD_NOT_FOUND`）
7. 窄视口 390px 横向溢出
8. **i18n 裸键外泄扫描**（页面出现 `settings.evaluation.…` 形态的未翻译键即判 FAIL）
