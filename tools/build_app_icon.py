"""Render the editable SVG into the PNG preview and multi-resolution Windows ICO."""
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PIL import Image

root = Path(__file__).resolve().parents[1] / 'resources'
renderer = QSvgRenderer(str(root / 'app-icon.svg'))
if not renderer.isValid():
    raise RuntimeError('Invalid icon SVG')
image = QImage(256, 256, QImage.Format.Format_ARGB32)
image.fill(Qt.GlobalColor.transparent)
painter = QPainter(image)
renderer.render(painter)
painter.end()
if not image.save(str(root / 'app-icon.png')):
    raise RuntimeError('Unable to save icon preview')
with Image.open(root / 'app-icon.png') as icon:
    icon.save(root / 'app-icon.ico', sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
