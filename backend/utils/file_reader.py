"""
上传文件文字提取工具。

职责：
- 根据上传文件的扩展名，选择 PDF 或 TXT 的读取方式；
- 将不同格式统一转换为纯文本，供财务智能体分析；
- 对空文件、编码错误和不支持的格式给出中文提示。

说明：
- PDF 读取逻辑复用 utils/pdf_reader.py；
- TXT 先根据文件头判断是否为带 BOM 的 UTF-16，
  否则依次尝试 UTF-8 和 GB18030，以兼容常见的中文文本文件。
"""

from pathlib import Path

from utils.pdf_reader import PdfExtractionError, extract_text_from_pdf


class FileTextExtractionError(RuntimeError):
    """
    上传文件提取文字失败时抛出的统一异常。

    网页层只需要捕获这个异常并展示中文原因，不需要了解底层是 PDF 还是 TXT。
    """


def _decode_txt(file_bytes: bytes) -> str:
    """
    把 TXT 文件的原始字节解码成中文字符串。

    参数：
        file_bytes: 用户上传的 TXT 文件原始内容。

    返回：
        str: 解码后的文本。

    异常：
        FileTextExtractionError: 文件为空、编码无法识别或内容为空时抛出。
    """
    if not file_bytes:
        raise FileTextExtractionError("上传的 TXT 文件是空的，请重新选择文件。")

    # UTF-16 的原始字节通常以 BOM（字节顺序标记）开头。
    # 必须先根据 BOM 判断，否则普通中文编码可能被 utf-16 错误解码成乱码。
    utf16_bom_prefixes = (b"\xff\xfe", b"\xfe\xff")
    if file_bytes.startswith(utf16_bom_prefixes):
        try:
            text = file_bytes.decode("utf-16")
        except UnicodeError as exc:
            raise FileTextExtractionError(
                "TXT 文件的 UTF-16 编码内容无法读取，请另存为 UTF-8 后重试。"
            ) from exc
    else:
        # 没有 UTF-16 BOM 时，优先按 UTF-8 读取；
        # 失败后再尝试中文 Windows 常见的 GB18030 编码。
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = file_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise FileTextExtractionError(
                "无法识别 TXT 文件的文字编码。请用记事本或编辑器另存为 UTF-8 后重试。"
            )

    # 去掉首尾空白，纯空白文本不能用于分析。
    clean_text = text.strip()
    if not clean_text:
        raise FileTextExtractionError("TXT 文件中没有可分析的有效文字。")

    return clean_text


def extract_text_from_uploaded_file(file_name: str, file_bytes: bytes) -> str:
    """
    根据文件名后缀，从用户上传的 PDF 或 TXT 文件中提取文字。

    参数：
        file_name: 用户上传时的原始文件名，用于判断文件类型。
        file_bytes: 上传文件的原始字节内容。

    返回：
        str: 提取出的纯文本。

    异常：
        FileTextExtractionError: 文件为空、格式不支持、PDF 解析失败，
                                 或 TXT 编码无法识别时抛出。
    """
    if not file_name:
        raise FileTextExtractionError("没有获取到文件名，请重新上传文件。")

    if not file_bytes:
        raise FileTextExtractionError("上传的文件内容为空，请重新选择文件。")

    # 统一转成小写后缀，确保 .PDF、.TXT 等大写扩展名也能识别。
    suffix = Path(file_name).suffix.lower()

    if suffix == ".pdf":
        try:
            # PDF 的详细错误由 pdf_reader 负责，这里统一包装成文件读取异常，
            # 方便网页层只捕获一种异常类型。
            return extract_text_from_pdf(file_bytes)
        except PdfExtractionError as exc:
            raise FileTextExtractionError(str(exc)) from exc

    if suffix == ".txt":
        return _decode_txt(file_bytes)

    raise FileTextExtractionError(
        "暂不支持这种文件格式，请上传 PDF 或 TXT 文件。"
    )
