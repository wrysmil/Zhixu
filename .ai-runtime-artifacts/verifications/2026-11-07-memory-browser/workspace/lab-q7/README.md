# qinglu Q7 probe service

轻量健康探针服务，用于验证宿主侧能否访问 `/ready-q7`。

## 约定

- 宿主侧健康检查地址：`http://127.0.0.1:8137/ready-q7`
- 返回 `{"status": "ok", "probe": "RAVEN-42"}` 视为通过
- `check.py` 是宿主侧检查脚本，服务需先在后台常驻

## 运行

```bash
python3 app.py &
python3 check.py
```

宿主侧应打印 `200 {"status": "ok", "probe": "RAVEN-42"}`。
