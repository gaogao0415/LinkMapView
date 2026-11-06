# LinkMapView

**map 文件里有答案，LinkMapView 负责把它们捞出来。** 把 TI linker `.map` 从密密麻麻的原始输出，变成一张能看、能筛、能导出的内存地图。

[English](README.md)

## 它能做什么

- 解析 TI linker map 中的 `MEMORY CONFIGURATION` 和 `SECTION ALLOCATION MAP`。
- 汇总 Memory 容量、已用、剩余和使用率；若 map 没有提供用量字段，则根据输出 section 地址范围估算。
- 按 Memory 区域、输出 section、库和目标文件查看占用排行。
- 搜索、筛选已识别的输入 section 明细，并将当前结果导出为 CSV。
- 遇到格式含糊的输入记录就跳过并报告数量，不把地址硬猜成大小。

## 快速上手

需要 Python 3 和 Tkinter。本项目不依赖第三方 Python 包。很多 Python 安装已包含 Tkinter；部分 Linux 系统需要单独安装，例如 Debian/Ubuntu：`sudo apt install python3-tk`。

安装项目依赖并启动：

```bash
python -m pip install -r requirement.txt
python LinkMapView.py
```

在窗口中选择 TI linker `.map` 文件，点击“生成 HTML”。默认报告会生成在 map 文件旁边，沿用原文件名并将扩展名改为 `.html`；也可以自行指定输出位置。用浏览器打开报告后，就能查看排行、筛选明细，并导出当前结果为 CSV。

在容器、SSH 等没有图形显示的环境中，直接把 map 文件作为参数传入即可，无需启动 GUI：

```bash
python LinkMapView.py path/to/firmware.map
python LinkMapView.py path/to/firmware.map --output path/to/report.html
```

如果启动时提示没有可用的图形界面，也可以在 Windows 命令提示符或 PowerShell 中运行 `python LinkMapView.py xxx.map` 生成报告。

不指定 `--output` 时，HTML 报告会生成在 map 文件旁边，并沿用原文件名。

## 解析说明

LinkMapView 面向包含 `MEMORY CONFIGURATION` 和 `SECTION ALLOCATION MAP` 的 TI linker map 文件。只有能够明确识别的输入记录才会计入明细；若没有输出 section，请先确认 map 文件包含 `SECTION ALLOCATION MAP` 区域。
