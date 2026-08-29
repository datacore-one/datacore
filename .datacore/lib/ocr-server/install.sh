#!/usr/bin/env bash
# Install system and Python dependencies for the Datacore OCR MCP server.
set -euo pipefail

echo "==> Installing system packages (Tesseract + Poppler)..."
sudo apt-get install -y tesseract-ocr poppler-utils

echo "==> Installing Python packages..."
pip install pytesseract pdf2image Pillow "mcp>=1.0.0"

echo "==> Verifying..."
tesseract --version | head -1
python3 -c "import pytesseract, pdf2image; print('Python deps OK')"

echo "Done. Run the server with: python3 server.py"
