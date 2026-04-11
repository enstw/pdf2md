#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "pymupdf4llm",
#   "pymupdf",
# ]
# ///

import sys
import os
import fitz
import pymupdf4llm
import re

def is_mostly_gibberish(text):
    if not text:
        return True
    # Count Chinese characters
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    total_chars = len(text.strip())
    if total_chars == 0:
        return True
    # If less than 10% are Chinese but it is a long text, it might be garbled or English-only
    # But since we expect Traditional Chinese, a low ratio is a red flag for this specific project
    ratio = chinese_chars / total_chars
    return ratio < 0.2 and total_chars > 50

def convert_with_transition_markers(pdf_path, output_md_path, page_offset=0, force_ocr=False):
    doc = fitz.open(pdf_path)
    
    # We still use pymupdf4llm for structure if possible, but we will override text if needed
    md_chunks = pymupdf4llm.to_markdown(doc, page_chunks=True)

    with open(output_md_path, "w", encoding="utf-8") as f:
        prev_label = None

        for i, chunk in enumerate(md_chunks):
            physical_idx = chunk['metadata'].get('page_number', 1) - 1
            
            try:
                current_label = doc[physical_idx].get_label()
            except:
                current_label = None

            if not current_label:
                current_label = str(physical_idx + 1)

            if page_offset:
                current_label = str(int(current_label) + page_offset)
            
            if prev_label is None:
                f.write(f"**[Page {current_label} start]**\n\n")
            else:
                f.write(f"\n\n**[Page {prev_label} end, Page {current_label} start]**\n\n")
            
            text = chunk.get('text', '').strip()
            
            # Decision: should we OCR?
            should_ocr = force_ocr or is_mostly_gibberish(text) or not text
            
            if should_ocr:
                try:
                    # Render page to high-res image (3x zoom = approx 216 DPI)
                    pix = doc[physical_idx].get_pixmap(matrix=fitz.Matrix(3, 3))
                    # Use Tesseract via PyMuPDF
                    ocr_pdf_bits = pix.pdfocr_tobytes(language="chi_tra+eng")
                    ocr_doc = fitz.open("pdf", ocr_pdf_bits)
                    text = ocr_doc[0].get_text().strip()
                    if text:
                        f.write(f"<!-- Forced OCR (chi_tra+eng) used for page {current_label} -->\n")
                except Exception as e:
                    print(f"  - OCR failed on page {current_label}: {e}", file=sys.stderr)

            if not text:
                f.write(f"*[WARNING: No text found on page {current_label}.]*\n")
            else:
                f.write(text)
            
            prev_label = current_label
            
        if prev_label is not None:
            f.write(f"\n\n**[Page {prev_label} end]**\n")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Convert PDF to Markdown with OCR support.")
    parser.add_argument("input", help="Input PDF path")
    parser.add_argument("output", help="Output Markdown path")
    parser.add_argument("--offset", type=int, default=0, help="Page number offset")
    parser.add_argument("--force-ocr", action="store_true", help="Force OCR on all pages")
    
    args = parser.parse_args()
    
    convert_with_transition_markers(args.input, args.output, page_offset=args.offset, force_ocr=args.force_ocr)
