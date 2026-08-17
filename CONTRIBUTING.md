# 参与贡献

谢谢你愿意改进这个观察台。请先读根目录 [README.md](README.md) 里的免责声明：这是本地筛选工具，不是实盘交易系统。

## 开发约定

- 不要加入自动下单、读取 API Key、或把失败回测包装成「可实盘配置」。
- 公开行情扫描必须可在无密钥环境下运行。
- 改网页静态资源后，给 `dashboard/templates/index.html` 里的 `css` / `js` 查询参数加版本号，避免浏览器缓存旧文件。
- macOS 控制面板改 `scripts/dashboard_gui.swift` 后运行 `./scripts/build_macos_app.sh`。
- Windows 控制面板改 `scripts/dashboard_gui.ps1` / `launch_dashboard.ps1` / `stop_dashboard.ps1`。

## 测试

在仓库根目录：

```bash
docker compose run --rm --no-deps --entrypoint python freqtrade \
  -m unittest discover -s tests -v
```

容器里 `scripts/` 可能是只读的，不要依赖写入 `.pyc`。

## 提交

说明「为什么改」，而不是只列文件名。不要提交 `.env`、密钥、本地 `user_data/data/` 行情缓存或 `PROJECT_CONTEXT.md` 这类私人交接笔记。
