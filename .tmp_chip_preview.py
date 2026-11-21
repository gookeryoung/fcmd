"""临时预览：离屏渲染对话 / 画图模式胶囊，确认圆角与配色。"""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout

from fcmd.gui import theme as guitheme
from fcmd.gui.qt import theme as qttheme
from fcmd.gui.qt.widgets import ToggleChip

app = QApplication([])
qttheme.apply_theme(app, guitheme.resolve_theme("dark-blue"))

frame = QFrame()
frame.setObjectName("ChatComposer")
frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
row = QHBoxLayout(frame)
row.setContentsMargins(12, 10, 12, 10)
row.setSpacing(6)
chat = ToggleChip("对话", icon="chat", tooltip="对话")
chat.setChecked(True)
paint = ToggleChip("画图", icon="paint", tooltip="画图")
row.addWidget(chat)
row.addWidget(paint)
row.addStretch(1)
frame.resize(360, 56)
frame.show()
app.processEvents()
out = Path(os.environ["TEMP"]) / "fcmd_chip_preview.png"
frame.grab().save(str(out))
print(out)
