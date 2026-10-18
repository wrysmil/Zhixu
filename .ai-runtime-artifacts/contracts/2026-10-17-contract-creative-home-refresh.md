---
artifact: contract
route: api-and-interface-design
skills: [source-driven-development]
source: [webui/src/components/creative/ArticleList.tsx]
created_at: 2026-10-17
status: approved
---
# 首页并行工作边界

- ArticleList Props 与现有 onCreate(title, templateId) 返回结构保持不变。
- WU-01 只写 ArticleList.tsx；WU-02 只写 locales 与首页测试、关联选择器；WU-03 只写 creative-writing-desk.png。
- 表单 testid=create-article-form；标题 label 使用 creative.topic_label，zh-CN=文章标题、en=Article title。
- create_btn：zh-CN=创建草稿、en=Create draft；creating_btn：zh-CN=创建中…、en=Creating…。
- 新增键：title/new_placeholder/create_btn/creating_btn/search_placeholder/status_filter/empty_template/selected_template/title_hint/library_hint/empty_action/clear_filters/dismiss/no_summary/revision_label/duplicate/delete/edit_article/confirm_delete/confirm_delete_body/cancel/confirm/new_article/template_hint/input_hint。
- selected_template 插值 {{name}}；revision_label 插值 {{revision}}；confirm_delete_body 插值 {{title}}。
- 新版占位文案只接收标题；input_hint 明确创建后可添加素材、整理正文；template_hint 明确只初始化提纲。
- 保留既有 template-*、status-filter-*、open-article-*、duplicate-article-*、delete-article-*、article-list-*、create-error 与删除警告 testid。
- 图片模块路径 @/assets/creative-writing-desk.png；装饰性 alt 空文本、aria-hidden=true。
