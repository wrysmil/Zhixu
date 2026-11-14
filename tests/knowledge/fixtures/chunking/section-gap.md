# 万能视频下载网站 - 方案设计文档

## 1. 技术架构

```
┌──────────────────────────────────────────────────────────┐
│                     用户浏览器                            │
│  ┌─────────────────────────────────────────────────────┐ │
│  │           Vue3 + Vite + Tailwind CSS               │ │
│  │  URL输入 → 解析展示 → 格式选择 → 下载              │ │
│  │                     ↘ AI总结 → 摘要/字幕/导图/问答  │ │
│  └──────────────────────┬──────────────────────────────┘ │
└─────────────────────────┼────────────────────────────────┘
                          │ HTTP API / SSE
┌─────────────────────────┼────────────────────────────────┐
│                  FastAPI 后端                              │
│  ┌──────────────────────┴──────────────────────────────┐ │
│  │  /api/parse      → 解析视频信息                      │ │
│  │  /api/download   → 服务端下载并提供文件              │ │
│  │  /api/direct-url → 获取视频直链                      │ │
│  │  /api/summarize  → AI视频总结（SSE流式）             │ │
│  │  /api/chat       → AI视频问答（SSE流式）             │ │
│  └──────────┬─────────────────────────┬──────────────┘ │
│             │                         │                  │
│  ┌──────────┴──────────┐  ┌───────────┴──────────────┐ │
│  │   yt-dlp 封装层      │  │   AI 总结模块             │ │
│  │   extract_info()     │  │   SubtitleExtractor       │ │
│  │   download()         │  │   → 字幕提取(yt-dlp/B站)  │ │
│  │   get_url()          │  │   VideoSummarizer         │ │
│  │                      │  │   → DeepSeek API 总结/问答 │ │
│  └──────────────────────┘  └────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

## 2. 技术栈选型

| 层次 | 技术 | 选型理由 |
|------|------|----------|
| 前端框架 | Vue 3 + Vite | 轻量、上手快、生态成熟 |
| CSS 框架 | Tailwind CSS | 快速构建精美 UI，无需写大量自定义 CSS |
| Markdown 渲染 | marked | 零依赖、高性能、浏览器端 Markdown 转 HTML |
| 思维导图 | markmap-lib + markmap-view | 开源成熟，支持 Markdown 输入，交互性好 |
| 后端框架 | FastAPI (0.135+) | 异步高性能、原生 SSE 支持、Python 生态 |
| 核心能力 | yt-dlp | 14w+ Star，支持 1800+ 网站，社区活跃 |
| AI 大模型 | DeepSeek (deepseek-chat) | 兼容 OpenAI 协议，通过 openai SDK 调用 |
| 数据库 | 无 | 轻量设计，无需持久化存储 |

## 3. API 设计

### 3.1 解析视频信息

```
POST /api/parse
Content-Type: application/json

Request:
{
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
}

Response (200):
{
    "success": true,
    "data": {
        "id": "dQw4w9WgXcQ",
        "title": "视频标题",
        "thumbnail": "https://i.ytimg.com/vi/xxx/maxresdefault.jpg",
        "duration": 212,
        "duration_string": "3:32",
        "uploader": "上传者名称",
        "platform": "YouTube",
        "view_count": 1500000,
        "upload_date": "20240101",
        "formats": [
            {
                "format_id": "137+140",
                "ext": "mp4",
                "resolution": "1920x1080",
                "height": 1080,
                "filesize": 52428800,
                "filesize_approx": 52428800,
                "vcodec": "avc1",
                "acodec": "mp4a",
                "label": "1080p MP4 (50MB)"
            }
        ],
        "subtitles": ["en", "zh-Hans", "ja"]
    }
}

Response (400):
{
    "success": false,
    "error": "无法解析该链接，请检查URL是否正确"
}
```

### 3.2 下载视频（服务端代理模式）

```