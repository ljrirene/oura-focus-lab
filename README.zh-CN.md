# Oura Focus Lab

[English](README.md)

一个本地优先的 Oura API V2 数据工具：下载个人历史数据、探索睡眠和恢复规律，并把最新一晚转换成当天可以直接执行的工作、运动与睡眠计划。

浏览器应用只在本机运行。OAuth token、健康 CSV、个人配置、每日项目记录和生成的分析报告默认都不会进入 Git。

> 本项目是独立社区项目，与 Oura Health Oy 无隶属或认可关系。它用于个人探索，不提供诊断或治疗建议。

## 功能

- Oura API V2 OAuth 授权与自动刷新
- 分页拉取历史数据，处理速率限制
- 保存原始 JSON，并增量合并 CSV
- 后台滚动同步最近数据，并显示成功、进行中和重试状态
- 可选的 AI 每日动态日程，只发送精简的 Oura 汇总
- 本地查看 Readiness、睡眠、HRV、趋势和具体日程
- 用户自行添加药品、补剂或日常项目，一键确认并仅保存在本机
- 导出系统日历提醒
- 可安装到手机主屏幕的 PWA 外壳
- 生成认知状态、纵向恢复和睡眠周期探索报告
- 运行时只使用 Python 标准库

## 快速开始

需要 Python 3.8 或更高版本，以及一个 [Oura OAuth 应用](https://cloud.ouraring.com/oauth/applications)。Redirect URI 设置为：

```text
http://localhost:8765/callback
```

创建本地配置：

```bash
cp .env.example .env
cp config/profile.example.json config/user.json
```

在 `.env` 中填写 Oura client ID 和 secret，然后授权：

```bash
python3 scripts/oura_sync.py auth
```

同步历史数据：

```bash
python3 scripts/oura_sync.py sync --start-date 2023-01-01
```

启动应用：

```bash
python3 scripts/oura_app.py
```

打开 `http://127.0.0.1:8787`。

## 隐私

以下内容默认被 Git 忽略：

```text
.env
.oura/
data/raw/
data/csv/*.csv
data/cognitive_log.csv
data/daily_item_log.json
data/exports/
reports/*.md
config/user.json
```

公开推送前执行：

```bash
git status --short
git check-ignore .env .oura/tokens.json data/csv/sleep.csv
```

不要使用 `git add -f` 添加这些文件。完整说明见 [隐私与安全模型](docs/PRIVACY.md)。

## 个性化配置

`config/profile.example.json` 是不含个人信息的模板。复制为被 Git 忽略的 `config/user.json`，再填写自己的时区、目标、睡眠阶段、工作日、运动计划和可选每日项目。药品、补剂或其他项目也可以直接在应用的“数据”页添加和删除。详见 [配置说明](docs/CONFIGURATION.md)。

如需启用每日动态计划，在 OpenAI Platform 创建 API key，并把 `OPENAI_API_KEY` 写入 `.env`。API 不可用时应用自动回退到本地规则。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/*.py
node --check app/app.js
```

## 文档

- [技术架构](docs/ARCHITECTURE.md)
- [配置说明](docs/CONFIGURATION.md)
- [隐私与安全](docs/PRIVACY.md)
- [部署与手机预览](docs/DEPLOYMENT.md)
- [代码审查](docs/CODE_REVIEW.md)
- [应用路线图](ROADMAP.md)
- [贡献指南](CONTRIBUTING.md)

## License

[MIT](LICENSE)
