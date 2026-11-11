"""
PDF 文字提取工具。

用途：
- 接收用户在网页上传的 PDF 文件字节内容；
- 逐页提取其中的文字；
- 为财务智能体提供可分析的纯文本。

限制说明：
- 本工具只能读取 PDF 中已有的“文字层”；
- 对于扫描件或图片型 PDF，需要先做 OCR（光学字符识别），本工具无法直接识别图片文字。
"""

from io import BytesIO

from pypdf import PdfReader


class PdfExtractionError(RuntimeError):
    """
    PDF 文字提取失败时抛出的统一异常。

    网页层可以直接捕获这个异常，并把中文错误信息展示给用户。
    """


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    从 PDF 文件的字节内容中逐页提取文字。

    参数：
        pdf_bytes: 通过 Streamlit 上传组件得到的 PDF 原始字节内容。

    返回：
        str: 按页拼接后的纯文本。
             每页前面会加上“--- 第 X 页 ---”，方便核对数据来源。

    异常：
        PdfExtractionError: 文件为空、PDF 损坏、PDF 需要密码，
                            或整份文件都提取不到文字时抛出。
    """
    # 先检查文件内容是否为空，避免把空数据交给 PDF 解析器。
    if not isinstance(pdf_bytes, (bytes, bytearray)) or not pdf_bytes:
        raise PdfExtractionError("上传的 PDF 文件内容为空，请重新选择文件。")

    # PDF 解析放在独立的 try 中，便于区分“文件本身无法打开”
    # 和“文件能打开但页面文字提取失败”两类问题。
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
    except Exception as exc:
        raise PdfExtractionError(
            "无法读取这个 PDF。请确认文件没有损坏，并且确实是 PDF 格式。"
        ) from exc

    try:
        # 有些 PDF 虽然设置了加密，但允许用空密码打开；
        # 这里先尝试空密码，如果仍失败，则提示用户更换未加密的 PDF。
        if reader.is_encrypted:
            try:
                password_result = reader.decrypt("")
            except Exception as exc:
                raise PdfExtractionError(
                    "这个 PDF 需要密码才能打开，请先解除密码保护后再上传。"
                ) from exc

            if not password_result:
                raise PdfExtractionError(
                    "这个 PDF 需要密码才能打开，请先解除密码保护后再上传。"
                )

        # 按页保存提取成功的文字，页与页之间保留明确分隔。
        extracted_pages: list[str] = []

        # 记录没有提取到文字或提取失败的页，最后统一提示，
        # 避免用户误以为所有材料都已完整读取。
        unreadable_pages: list[int] = []

        for page_number, page in enumerate(reader.pages, start=1):
            try:
                page_text = page.extract_text()
            except Exception:
                # 单页提取失败时不中断整份文件，而是记录下来继续处理其他页；
                # 这样一份 PDF 中少量异常页不会导致整体无法分析。
                unreadable_pages.append(page_number)
                continue

            clean_text = (page_text or "").strip()

            if clean_text:
                extracted_pages.append(
                    f"--- 第 {page_number} 页 ---\n{clean_text}"
                )
            else:
                # 空白页或扫描图片页通常提取不到文字，也记录下来。
                unreadable_pages.append(page_number)

        # 如果所有页面都没有文字，说明大概率是扫描件或加密限制，
        # 应给出明确提示，而不是返回空字符串继续调用大模型。
        if not extracted_pages:
            raise PdfExtractionError(
                "没有从这份 PDF 中提取到任何文字。它可能是扫描图片版，"
                "需要先用 OCR 工具转换为可复制的文字后再上传。"
            )

        # 用两个换行连接各页，排版更清晰，方便大模型识别页与页的边界。
        result = "\n\n".join(extracted_pages)

        # 如果只有部分页面无法读取，在文本末尾补充提示。
        # 这段提示也会出现在网页的原文预览中，方便用户检查材料是否完整。
        if unreadable_pages:
            # 页数太多时只列前 20 页，避免提示文字本身过长。
            shown_pages = "、".join(str(number) for number in unreadable_pages[:20])
            if len(unreadable_pages) > 20:
                shown_pages += " 等"
            result += (
                "\n\n[提取提示：第 "
                f"{shown_pages} 页未提取到文字，可能是扫描图片页或空白页。]"
            )

        return result

    except PdfExtractionError:
        # 这是上面主动写好的中文提示，直接向上传递，避免被下面的通用异常覆盖。
        raise

    except Exception as exc:
        # 处理页面级异常之外的意外情况，例如 PDF 结构异常导致无法遍历页面。
        raise PdfExtractionError(
            "读取 PDF 页面时发生异常，请更换文件或重新导出 PDF 后再试。"
        ) from exc
