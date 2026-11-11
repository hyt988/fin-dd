# 内置中文字体说明

本目录存放 PDF 导出使用的中文字体。字体文件随代码一起提交到 Git 仓库，
部署到 Linux 服务器（Render）后无需依赖操作系统自带字体。

## 文件清单

| 文件 | 用途 |
|---|---|
| `NotoSansSC-Regular.ttf` | 正文、表格数据的常规体（字重 400） |
| `NotoSansSC-Bold.ttf` | 报告标题、表头的粗体（字重 700） |
| `LICENSE-NotoSansSC.txt` | SIL Open Font License 1.1 许可证全文 |

## 字体来源

- 字体：Noto Sans SC（思源黑体的开源发行版，由 Adobe 与 Google 联合开发）
- 项目主页：https://github.com/notofonts/noto-cjk
- 原始文件：`Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf`（可变字体）
- 许可证：SIL Open Font License 1.1，
  允许免费商用、修改和随项目再分发；再分发时必须保留许可证文件。

## 为什么用 .ttf 而不是 .otf

本项目使用 fpdf2 生成 PDF。实测发现：CFF 轮廓的 `.otf` 字体虽然能显示，
但生成的 PDF 在复制文字、搜索文字或交给 pdfplumber 解析时会出现乱码或
提取不到文字；TrueType 轮廓的 `.ttf` 字体没有这个问题。
因此这里放的是由官方可变字体转换出来的静态 `.ttf`。

## 如何重新生成这两个文件

原始仓库只提供 `.otf` 和可变字体 `.ttf`，没有现成的静态 `.ttf`。
重新生成方法（在本机已安装 fontTools 的前提下）：

```python
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

source = "NotoSansSC-VF.ttf"  # 从上面仓库的 Sans/Variable/TTF/Subset 下载
for weight, style in [(400, "Regular"), (700, "Bold")]:
    font = TTFont(source)
    static = instantiateVariableFont(font, {"wght": weight}, updateFontNames=True)
    static.save(f"NotoSansSC-{style}.ttf")
```

两个文件名必须保持不变，因为 `utils/pdf_generator.py` 是按文件名
定位字体的；如果改了文件名，需要同步修改该文件顶部的
`_REGULAR_FONT_PATH` 和 `_BOLD_FONT_PATH` 两个常量。
