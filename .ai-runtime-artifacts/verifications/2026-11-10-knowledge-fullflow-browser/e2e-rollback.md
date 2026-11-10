# 运维手册（E2E 全流程回归语料）

## 线上版本回滚

线上版本出问题时，优先按以下步骤回滚：

1. 确认当前版本：kubectl rollout history deployment/web-api
2. 回滚到上一个版本：kubectl rollout undo deployment/web-api
3. 观察滚动状态：kubectl rollout status deployment/web-api
4. 如果仍然异常，回滚到指定版本：kubectl rollout undo deployment/web-api --to-revision=3

```bash
kubectl rollout undo deployment/web-api
kubectl rollout status deployment/web-api
```

回滚完成后在值班群同步事故时间线与根因。

## 退款说明

用户申请退款后，原路退回一般 1-3 个工作日到账；跨行或境外卡最长 7 个工作日。超过 7 个工作日未到账，请联系客服核对支付流水号。

| 支付方式 | 到账时间 |
| --- | --- |
| 余额 | 即时到账 |
| 银行卡 | 1-3 个工作日 |
| 境外卡 | 最长 7 个工作日 |

## 常见问题

- 回滚后数据是否丢失：不会，回滚只切换镜像版本，不修改数据库。
- 退款是否收费：不收取手续费，按支付渠道原路退回。
