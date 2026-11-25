"""OCR 测试集工具（规格 docs/ryugi/specs/2026-11-23-ocr-dataset-design.md）：标注格式与校验、挑帧、AI 标注、待定框核对、评测。

    python -m tools.ocr_dataset <子命令> …     # 子命令见 python -m tools.ocr_dataset -h

数据仓库（私有，图片走 Git LFS）位置由 HIRO_OCR_DATASET 指定，缺省 ../hiro-ocr-dataset。
"""
