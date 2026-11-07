# TrendRadar Local · 本地新闻雷达

基于 [TrendRadar](https://github.com/sansan0/TrendRadar) 6.11.0 的社区扩展。用同一个面板浏览新闻、官方公示、事件原文和待核线索，并通过定时任务刷新重点关注。

- **可配置关注**：在页面分别设置关键词和区域。关键词和区域初始为空，由部署者填写。单组命中即可收录，两组同时命中的报道优先。可选的 Google 新闻索引采集默认关闭。
- **公示与事件**：可以在 RSS 配置中添加本地政务来源。面板从采集结果识别符合条件的政务原文。事件页保留原文、时间与来源，区分官方发布、可识别的独立报道、索引与转载。
- **社交热点**：展示选定的 Telegram 公开频道与第三方 X 全球榜单快照。社交内容和路边社线索单独展示。
- **自动更新**：每半小时采集一次。关注、事件、信源和社交结果可通过只读的 `/intelligence.json` 获取。
- **可选 AI**：支持 MiniMax M Plan 的 M3.1 Flash 套餐密钥。按北京时间每日最多 4 个时段，并执行本地 ¥0.20 预留上限。默认关闭。

## 运行

需要 Docker Engine 和 Docker Compose v2 或更新版本。

```bash
git clone https://github.com/satantqr0/trendradar-local.git
cd trendradar-local
docker compose up -d --build
docker compose logs -f trendradar
```

首轮采集完成后访问 [首页](http://127.0.0.1:8080/) 或 [重点关注](http://127.0.0.1:8080/#focus)。如本机端口被占用，使用 `PORT=18080 docker compose up -d`。

首次启动会在独立数据卷生成管理口令。读取后将它填入页面的「重点关注 → 编辑关注设置」。保存的设置将在下一轮采集使用。

```bash
docker compose exec -T trendradar cat /app/focus_private/admin.key
```

默认只监听宿主机回环地址。需要在可信内网的其他设备访问时，可用 `BIND_ADDR=<NAS 局域网地址> docker compose up -d`。直接打开该地址，或使用受保护的 HTTPS 反向代理。面板、关注配置的读取接口和 JSON 报告由同一站点提供，修改配置需要管理口令。请将面板限制在可信网络或经访问控制的代理后面。

报告和 SQLite 数据保存在 `reports` 数据卷。管理口令和关注配置保存在 `focus` 数据卷。编辑 `config/config.yaml` 或 `config/frequency_words.txt` 可以修改基础采集源和上游关键词，修改后执行 `docker compose up -d --build`。

## 启用 AI 分析（可选）

把自己的 MiniMax M Plan 套餐密钥保存为 `secrets/minimax.key`，并限制文件权限。然后初始化本地预算账本，再启用 AI。

```bash
chmod 600 secrets/minimax.key
docker compose run --rm --no-deps --entrypoint python trendradar /app/local_tools/initialize_budget.py
AI_ANALYSIS_ENABLED=true docker compose up -d
```

初始化当天暂停新增分析，下一自然日起按 08:00、12:30、18:30、22:00 四个窗口运行，每个窗口最多一次。若已有账本，初始化会拒绝覆盖。¥0.20 是本应用的本地保守预留上限，MiniMax 的套餐与积分按供应商规则结算。若希望重建容器后仍启用 AI，可在本地 `.env` 设置 `AI_ANALYSIS_ENABLED=true`。

## 测试与许可

```bash
python3 -m pip install -r requirements-dev.txt
PYTHONPATH=custom_layout python3 -m unittest discover -s tests -v
```

测试使用构造的输入。运行时的公开来源可能更新页面结构或限制访问，面板会显示来源状态及缓存状态。

本项目按 [GPL-3.0](LICENSE) 发布。基础采集、分析器和 HTML 生成器源自 [sansan0/TrendRadar 的对应源码](https://github.com/sansan0/TrendRadar/tree/ceabb027fe9bbdafec59dbbfa12cb46c2ba256ce)。本仓库包含社区扩展和容器构建文件，未发布容器镜像。详见 [上游与修改说明](NOTICE.md)。
