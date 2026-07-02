"""Muestra texto crudo de páginas seleccionadas de un PDF."""
import sys
import pdfplumber

pdf_path = sys.argv[1] if len(sys.argv) > 1 else "data/raw_pdfs/DA-5-2024.pdf"
pages = [0, 1, 2, 3, 4, 10, 20, 50]  # índices 0-based

with pdfplumber.open(pdf_path) as pdf:
    print(f"Total páginas: {len(pdf.pages)}")
    for i in pages:
        if i >= len(pdf.pages):
            break
        texto = pdf.pages[i].extract_text() or ""
        print(f"\n{'='*60}")
        print(f"PÁGINA {i+1} ({len(texto)} chars)")
        print('='*60)
        print(texto[:1500])