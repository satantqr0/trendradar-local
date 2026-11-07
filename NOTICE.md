# 上游与修改说明

本项目包含对 [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar) 的修改，许可证沿用 GPL-3.0，完整文本见 [LICENSE](LICENSE)。

| 项目 | 对应版本 |
| --- | --- |
| 上游版本 | TrendRadar 6.11.0 |
| 源码提交 | [ceabb027fe9bbdafec59dbbfa12cb46c2ba256ce](https://github.com/sansan0/TrendRadar/tree/ceabb027fe9bbdafec59dbbfa12cb46c2ba256ce) |
| 基础镜像摘要 | `sha256:3effcb5cedf5c385b50a6a32cd2599cbdbf54c9d8c52c0403406e356f88df80b` |
| 扩展发布日期 | 2026-11-07 |

`custom_layout/manage_focus.py` 由上游 `docker/manage.py` 修改，接入带关注配置接口的本地 Web 服务。测试的两份 SQLite schema 直接来自上述提交。Docker 构建时保存基础镜像中的上游 HTML 渲染器与分析器，扩展通过这两个入口补充阅读界面、可配置关注、公开公示与社交来源、事件聚合及可选的预算分析。新闻和公告仍归各自来源，面板提供简短标题与原文链接。
