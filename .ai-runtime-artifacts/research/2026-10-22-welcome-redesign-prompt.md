# 欢迎区横向插画与字体 Logo

用户要求重新设计欢迎文案和插画，同时左上角改用类似参考图 ChatGPT 的简洁字体图片。

字体 Logo：本机 Noto Sans SC Bold，字形转换为 SVG 路径，无字体运行时依赖。原字体元数据标记 SIL Open Font License 1.1。生成脚本位于本目录 `2026-10-22-create-zhixu-wordmark.py`，输出 `webui/public/brand/zhixu-wordmark.svg`。

首页插画：内置 image_gen，以已确认的浅蓝幼年猫头鹰为角色参考，输出经缩放编码为 `webui/public/illustrations/zhixu-welcome.webp`（552 × 368），保留透明度。未覆盖设置等页面使用的原 mascot。

```text
Use case: stylized-concept. Create a NEW horizontal welcome illustration asset for 知序 Zhixu, a calm personal AI workspace. The supplied image is a character identity reference only: preserve the same cute powder-blue baby owl, large navy eyes, cream face and tummy, tiny apricot beak and feet, soft blush, single cream-and-apricot curled forelock. Change pose and composition completely. Scene: the owl is comfortably leaning its body and one little wing on a short closed ivory notebook lying horizontally, slightly tilting its head toward the LEFT with a relaxed curious smile, while the other wing gently rests on a single small blank note card. No waving, no upright tall pose, no giant book held in front. Compact WIDE silhouette approximately 3:2, character occupies right two thirds, notebook extends softly to the left, all elements form one low grounded composition. Head and notebook form a gentle diagonal, broad simple shapes, thoughtful and welcoming instead of hyperactive. Soft matte ceramic/clay render, restrained dimensional lighting and fewer shiny reflections, a little cozy and cute, not photorealistic. Blue #89B9CE, creamy ivory #FFF4DE, dark navy eyes, tiny warm apricot accents. Actual transparent alpha, no backdrop, no ground plane, no cast shadow haze, no scenery, no rectangle, no badge, no text or letters or symbols or watermark. Full scene fully inside with 5% transparent margin, almost fills width. This is a small UI illustration shown at 184 by 124 CSS pixels beside a welcome heading; prioritize one readable silhouette, not little decorative details.
```
