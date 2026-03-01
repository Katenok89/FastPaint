import sys
import math
from dataclasses import dataclass
from enum import Enum, auto

from PySide6.QtCore import Qt, QPoint, QRect, QSize, QBuffer, QByteArray
from PySide6.QtGui import (
    QAction, QColor, QIcon, QImage, QPainter, QPen, QPixmap,
    QKeySequence, QGuiApplication, QPalette
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QColorDialog, QToolBar, QStatusBar,
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QSpinBox, QComboBox, QPushButton, QCheckBox, QMessageBox, QStyleFactory
)


class Tool(Enum):
    BRUSH = auto()
    ERASER = auto()
    LINE = auto()
    RECT = auto()
    ELLIPSE = auto()
    FILL = auto()
    EYEDROPPER = auto()


def clamp(v, a, b):
    return max(a, min(b, v))


from dataclasses import dataclass, field
from PySide6.QtGui import QColor

@dataclass
class BrushSettings:
    color: QColor = field(default_factory=lambda: QColor(30, 30, 30))
    width: int = 8
    alpha: int = 255
    hard: bool = True
    antialias: bool = True

class History:
    """Simple image history with undo/redo using PNG snapshots."""
    def __init__(self, limit: int = 40):
        self.limit = limit
        self._states: list[QByteArray] = []
        self._index = -1

    def _encode(self, image: QImage) -> QByteArray:
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.WriteOnly)
        image.save(buf, "PNG")
        buf.close()
        return ba

    def _decode(self, ba: QByteArray) -> QImage:
        img = QImage()
        img.loadFromData(ba, "PNG")
        return img

    def push(self, image: QImage):
        # drop redo states
        if self._index < len(self._states) - 1:
            self._states = self._states[: self._index + 1]

        self._states.append(self._encode(image))

        # trim
        if len(self._states) > self.limit:
            drop = len(self._states) - self.limit
            self._states = self._states[drop:]
            self._index = len(self._states) - 1
        else:
            self._index += 1

    def can_undo(self) -> bool:
        return self._index > 0

    def can_redo(self) -> bool:
        return self._index < len(self._states) - 1

    def undo(self) -> QImage | None:
        if not self.can_undo():
            return None
        self._index -= 1
        return self._decode(self._states[self._index])

    def redo(self) -> QImage | None:
        if not self.can_redo():
            return None
        self._index += 1
        return self._decode(self._states[self._index])

    def reset(self):
        self._states.clear()
        self._index = -1


class Canvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self.bg = QColor(210, 214, 220)
        self.checker_a = QColor(230, 230, 234)
        self.checker_b = QColor(248, 248, 252)

        self.settings = BrushSettings()
        self.tool = Tool.BRUSH

        self.zoom = 1.0
        self.show_grid = False
        self.grid_step = 25

        self._image = QImage(1400, 900, QImage.Format_ARGB32_Premultiplied)
        self._image.fill(Qt.transparent)

        self._preview = QImage(self._image.size(), QImage.Format_ARGB32_Premultiplied)
        self._preview.fill(Qt.transparent)

        self._drawing = False
        self._last = QPoint()
        self._start = QPoint()

        self.history = History(limit=50)
        self.history.push(self._image)

        self._cursor_pos = QPoint(-1, -1)

    def sizeHint(self):
        return QSize(1200, 800)

    def image(self) -> QImage:
        return self._image

    def set_image(self, img: QImage):
        if img.format() != QImage.Format_ARGB32_Premultiplied:
            img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self._image = img
        self._preview = QImage(self._image.size(), QImage.Format_ARGB32_Premultiplied)
        self._preview.fill(Qt.transparent)
        self.update()

    def new_image(self, w: int, h: int, transparent: bool = False):
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent if transparent else Qt.white)
        self.set_image(img)
        self.history.reset()
        self.history.push(self._image)

    def set_tool(self, tool: Tool):
        self.tool = tool
        self._preview.fill(Qt.transparent)
        self.update()

    def set_zoom(self, z: float):
        self.zoom = clamp(z, 0.1, 8.0)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # background
        p.fillRect(self.rect(), self.bg)

        # center the canvas
        img_w = int(self._image.width() * self.zoom)
        img_h = int(self._image.height() * self.zoom)
        x0 = (self.width() - img_w) // 2
        y0 = (self.height() - img_h) // 2
        target = QRect(x0, y0, img_w, img_h)

        # transparency checker behind image (useful if transparent canvas)
        self._draw_checker(p, target)

        # draw base image
        p.drawImage(target, self._image)

        # draw preview overlay
        if not self._preview.isNull():
            p.drawImage(target, self._preview)

        # grid
        if self.show_grid:
            self._draw_grid(p, target)

        # frame
        p.setPen(QPen(QColor(30, 30, 30, 140), 2))
        p.drawRect(target.adjusted(0, 0, -1, -1))

    def _draw_checker(self, painter: QPainter, target: QRect):
        s = 18
        # draw in widget coords (not image coords)
        for y in range(target.top(), target.bottom(), s):
            for x in range(target.left(), target.right(), s):
                c = self.checker_a if ((x // s + y // s) % 2 == 0) else self.checker_b
                painter.fillRect(QRect(x, y, s, s), c)

    def _draw_grid(self, painter: QPainter, target: QRect):
        step = max(5, int(self.grid_step * self.zoom))
        if step < 8:
            return
        pen = QPen(QColor(60, 60, 60, 35), 1)
        painter.setPen(pen)
        left, top, right, bottom = target.left(), target.top(), target.right(), target.bottom()
        x = left
        while x <= right:
            painter.drawLine(x, top, x, bottom)
            x += step
        y = top
        while y <= bottom:
            painter.drawLine(left, y, right, y)
            y += step

    def _widget_to_image(self, pos: QPoint) -> QPoint | None:
        img_w = int(self._image.width() * self.zoom)
        img_h = int(self._image.height() * self.zoom)
        x0 = (self.width() - img_w) // 2
        y0 = (self.height() - img_h) // 2
        x = pos.x() - x0
        y = pos.y() - y0
        if x < 0 or y < 0 or x >= img_w or y >= img_h:
            return None
        ix = int(x / self.zoom)
        iy = int(y / self.zoom)
        ix = clamp(ix, 0, self._image.width() - 1)
        iy = clamp(iy, 0, self._image.height() - 1)
        return QPoint(ix, iy)

    def _make_pen(self, color: QColor, width: int) -> QPen:
        c = QColor(color)
        c.setAlpha(self.settings.alpha)
        pen = QPen(c, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        return pen

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        ip = self._widget_to_image(event.position().toPoint())
        if ip is None:
            return

        self._cursor_pos = ip

        if self.tool == Tool.EYEDROPPER:
            col = QColor(self._image.pixel(ip))
            self.settings.color = col
            self.update()
            self.parent().statusBar().showMessage(f"Пипетка: {col.name(QColor.HexArgb)}", 2000)
            return

        if self.tool == Tool.FILL:
            self._commit_history_before_change()
            self._flood_fill(ip, self.settings.color, self.settings.alpha)
            self.update()
            self.history.push(self._image)
            return

        self._drawing = True
        self._last = ip
        self._start = ip

        if self.tool in (Tool.BRUSH, Tool.ERASER):
            self._commit_history_before_change()
            self._stroke_to(ip, ip)
            self.update()

        if self.tool in (Tool.LINE, Tool.RECT, Tool.ELLIPSE):
            self._preview.fill(Qt.transparent)
            self._update_preview(ip)
            self.update()

    def mouseMoveEvent(self, event):
        ip = self._widget_to_image(event.position().toPoint())
        if ip is None:
            self._cursor_pos = QPoint(-1, -1)
            self.update()
            return
        self._cursor_pos = ip

        if not self._drawing:
            self.update()
            return

        if self.tool in (Tool.BRUSH, Tool.ERASER):
            self._stroke_to(self._last, ip)
            self._last = ip
            self.update()
        elif self.tool in (Tool.LINE, Tool.RECT, Tool.ELLIPSE):
            self._update_preview(ip)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if not self._drawing:
            return

        ip = self._widget_to_image(event.position().toPoint())
        if ip is None:
            ip = self._last

        if self.tool in (Tool.LINE, Tool.RECT, Tool.ELLIPSE):
            # commit preview to image
            self._commit_history_before_change()
            p = QPainter(self._image)
            p.setRenderHint(QPainter.Antialiasing, self.settings.antialias)
            p.drawImage(0, 0, self._preview)
            p.end()
            self._preview.fill(Qt.transparent)
            self.update()
            self.history.push(self._image)

        if self.tool in (Tool.BRUSH, Tool.ERASER):
            self.history.push(self._image)

        self._drawing = False

    def _commit_history_before_change(self):
        # reserved for future optimizations; keep function for clarity
        pass

    def _stroke_to(self, a: QPoint, b: QPoint):
        p = QPainter(self._image)
        p.setRenderHint(QPainter.Antialiasing, self.settings.antialias)

        if self.tool == Tool.ERASER:
            # "erase" to transparent if image has alpha, else to white
            # We'll erase to transparent and composite over checker; saving keeps alpha.
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            pen = QPen(Qt.transparent, self.settings.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            p.drawLine(a, b)
        else:
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            pen = self._make_pen(self.settings.color, self.settings.width)
            p.setPen(pen)
            p.drawLine(a, b)
        p.end()

    def _update_preview(self, current: QPoint):
        self._preview.fill(Qt.transparent)
        p = QPainter(self._preview)
        p.setRenderHint(QPainter.Antialiasing, self.settings.antialias)
        pen = self._make_pen(self.settings.color, self.settings.width)
        p.setPen(pen)

        r = QRect(self._start, current).normalized()

        if self.tool == Tool.LINE:
            p.drawLine(self._start, current)
        elif self.tool == Tool.RECT:
            p.drawRect(r)
        elif self.tool == Tool.ELLIPSE:
            p.drawEllipse(r)

        p.end()

    def _flood_fill(self, start: QPoint, color: QColor, alpha: int):
        target = QColor(self._image.pixel(start))
        fill = QColor(color)
        fill.setAlpha(alpha)

        if target.rgba() == fill.rgba():
            return

        w = self._image.width()
        h = self._image.height()

        # get raw pixel access
        img = self._image
        visited = set()
        stack = [start]

        def same(c1: QColor, c2: QColor) -> bool:
            return c1.rgba() == c2.rgba()

        while stack:
            p = stack.pop()
            x, y = p.x(), p.y()
            if (x, y) in visited:
                continue
            visited.add((x, y))
            if x < 0 or y < 0 or x >= w or y >= h:
                continue
            if not same(QColor(img.pixel(x, y)), target):
                continue

            img.setPixelColor(x, y, fill)
            stack.append(QPoint(x + 1, y))
            stack.append(QPoint(x - 1, y))
            stack.append(QPoint(x, y + 1))
            stack.append(QPoint(x, y - 1))

    def keyPressEvent(self, event):
        # Ctrl + wheel zoom is on wheelEvent. Here just arrows nudge grid etc.
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        # Ctrl+wheel zoom
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            factor = 1.1 if delta > 0 else (1 / 1.1)
            self.set_zoom(self.zoom * factor)
            event.accept()
            return
        super().wheelEvent(event)

def force_light_theme(app: QApplication):
    # 1) Принудительно Fusion (иначе Windows тема может рулить цветами)
    app.setStyle(QStyleFactory.create("Fusion"))

    # 2) Светлая палитра с чёрным текстом
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#f5f6f8"))
    pal.setColor(QPalette.WindowText, QColor("#111111"))

    pal.setColor(QPalette.Base, QColor("#ffffff"))
    pal.setColor(QPalette.AlternateBase, QColor("#f2f3f5"))

    pal.setColor(QPalette.Text, QColor("#111111"))
    pal.setColor(QPalette.Button, QColor("#f0f1f3"))
    pal.setColor(QPalette.ButtonText, QColor("#111111"))

    pal.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipText, QColor("#111111"))

    pal.setColor(QPalette.Highlight, QColor("#3b82f6"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))

    # disabled (чтобы было видно, но бледнее)
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(0, 0, 0, 120))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(0, 0, 0, 120))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(0, 0, 0, 120))

    app.setPalette(pal)

class Inspector(QWidget):
    def __init__(self, canvas: Canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Color row
        color_row = QHBoxLayout()
        self.color_btn = QPushButton("Цвет")
        self.color_btn.setMinimumHeight(34)
        self.color_btn.clicked.connect(self.pick_color)
        self.color_preview = QLabel()
        self.color_preview.setFixedSize(34, 34)
        self.color_preview.setStyleSheet("border-radius: 6px; border: 1px solid rgba(0,0,0,0.25);")
        color_row.addWidget(self.color_btn)
        color_row.addStretch(1)
        color_row.addWidget(self.color_preview)
        root.addLayout(color_row)

        # Width
        width_row = QHBoxLayout()
        width_row.addWidget(QLabel("Толщина"))
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 120)
        self.width_spin.setValue(self.canvas.settings.width)
        self.width_spin.valueChanged.connect(self.on_width)
        width_row.addStretch(1)
        width_row.addWidget(self.width_spin)
        root.addLayout(width_row)

        # Alpha
        alpha_row = QVBoxLayout()
        alpha_header = QHBoxLayout()
        alpha_header.addWidget(QLabel("Прозрачность"))
        self.alpha_value = QLabel(str(self.canvas.settings.alpha))
        self.alpha_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        alpha_header.addStretch(1)
        alpha_header.addWidget(self.alpha_value)
        alpha_row.addLayout(alpha_header)

        self.alpha_slider = QSlider(Qt.Horizontal)
        self.alpha_slider.setRange(0, 255)
        self.alpha_slider.setValue(self.canvas.settings.alpha)
        self.alpha_slider.valueChanged.connect(self.on_alpha)
        alpha_row.addWidget(self.alpha_slider)
        root.addLayout(alpha_row)

        # Antialias
        self.aa_check = QCheckBox("Сглаживание")
        self.aa_check.setChecked(self.canvas.settings.antialias)
        self.aa_check.toggled.connect(self.on_aa)
        root.addWidget(self.aa_check)

        # Grid + Zoom
        grid_row = QHBoxLayout()
        self.grid_check = QCheckBox("Сетка")
        self.grid_check.setChecked(self.canvas.show_grid)
        self.grid_check.toggled.connect(self.on_grid)
        grid_row.addWidget(self.grid_check)
        grid_row.addStretch(1)
        root.addLayout(grid_row)

        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Зум"))
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(["25%", "50%", "75%", "100%", "125%", "150%", "200%", "300%", "400%"])
        self.zoom_combo.setCurrentText("100%")
        self.zoom_combo.currentTextChanged.connect(self.on_zoom_text)
        zoom_row.addStretch(1)
        zoom_row.addWidget(self.zoom_combo)
        root.addLayout(zoom_row)

        root.addStretch(1)

        self.refresh_color()

    def refresh_color(self):
        c = self.canvas.settings.color
        self.color_preview.setStyleSheet(
            f"background: {c.name()}; border-radius: 6px; border: 1px solid rgba(0,0,0,0.25);"
        )

    def pick_color(self):
        c = QColorDialog.getColor(self.canvas.settings.color, self, "Выбор цвета")
        if c.isValid():
            self.canvas.settings.color = c
            self.refresh_color()
            self.canvas.update()

    def on_width(self, v: int):
        self.canvas.settings.width = v

    def on_alpha(self, v: int):
        self.canvas.settings.alpha = v
        self.alpha_value.setText(str(v))

    def on_aa(self, on: bool):
        self.canvas.settings.antialias = on

    def on_grid(self, on: bool):
        self.canvas.show_grid = on
        self.canvas.update()

    def on_zoom_text(self, text: str):
        try:
            z = int(text.replace("%", "")) / 100.0
            self.canvas.set_zoom(z)
        except Exception:
            pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("FastPaint")
        self.resize(1400, 900)

        self.canvas = Canvas(self)
        self.setCentralWidget(self.canvas)

        self._build_actions()
        self._build_toolbar()
        self._build_menus()
        self._build_inspector()
        self._build_status()

        self._apply_theme()

        self._update_undo_redo()

    def _apply_theme(self):
        # аккуратный, взрослый стиль без вырвиглазных цветов
        self.setStyleSheet("""
            QMenuBar, QMenuBar::item { color: #111; background: #f5f6f8; }
            QMenu { color: #111; background: #ffffff; }
            QToolBar, QStatusBar { color: #111; background: #f5f6f8; }
            QToolButton { color: #111; }
            QToolButton:disabled { color: rgba(0,0,0,0.35); }
            QAction:disabled { color: rgba(0,0,0,0.35); }
            QMainWindow { background: #f5f6f8; }
            QToolBar { spacing: 6px; padding: 6px; }
            QToolBar QToolButton {
                padding: 6px 10px;
                border-radius: 8px;
            }
            QToolBar QToolButton:checked {
                background: rgba(0,0,0,0.08);
            }
            QDockWidget {
                titlebar-close-icon: none;
                titlebar-normal-icon: none;
            }
            QDockWidget::title {
                padding: 8px;
                background: rgba(0,0,0,0.04);
            }
            QPushButton {
                padding: 6px 10px;
                border-radius: 8px;
            }
            QSpinBox, QComboBox {
                padding: 4px 8px;
                border-radius: 8px;
            }
        """)

    def _build_status(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        sb.showMessage("Готово. Ctrl+Колесо — зум. Ctrl+S — сохранить. Ctrl+Z/Y — undo/redo.")

    def _build_inspector(self):
        dock = QDockWidget("Параметры", self)
        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        dock.setWidget(Inspector(self.canvas, dock))
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        dock.setMinimumWidth(260)

    def _build_actions(self):
        self.act_new = QAction("Новый…", self)
        self.act_new.setShortcut(QKeySequence.New)
        self.act_new.triggered.connect(self.new_file)

        self.act_open = QAction("Открыть…", self)
        self.act_open.setShortcut(QKeySequence.Open)
        self.act_open.triggered.connect(self.open_file)

        self.act_save = QAction("Сохранить как…", self)
        self.act_save.setShortcut(QKeySequence.Save)
        self.act_save.triggered.connect(self.save_file)

        self.act_export = QAction("Экспорт PNG (прозр.)…", self)
        self.act_export.triggered.connect(self.export_png_transparent)

        self.act_undo = QAction("Undo", self)
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_undo.triggered.connect(self.undo)

        self.act_redo = QAction("Redo", self)
        self.act_redo.setShortcut(QKeySequence.Redo)
        self.act_redo.triggered.connect(self.redo)

        self.act_copy = QAction("Копировать", self)
        self.act_copy.setShortcut(QKeySequence.Copy)
        self.act_copy.triggered.connect(self.copy_to_clipboard)

        self.act_paste = QAction("Вставить", self)
        self.act_paste.setShortcut(QKeySequence.Paste)
        self.act_paste.triggered.connect(self.paste_from_clipboard)

        # tools (checkable)
        self.tool_actions = {}

        def mk_tool(name, tool: Tool, shortcut: str):
            a = QAction(name, self)
            a.setCheckable(True)
            a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(lambda checked, t=tool: self.set_tool(t))
            self.tool_actions[tool] = a
            return a

        self.act_brush = mk_tool("Кисть (B)", Tool.BRUSH, "B")
        self.act_eraser = mk_tool("Ластик (E)", Tool.ERASER, "E")
        self.act_line = mk_tool("Линия (L)", Tool.LINE, "L")
        self.act_rect = mk_tool("Прямоуг. (R)", Tool.RECT, "R")
        self.act_ellipse = mk_tool("Эллипс (O)", Tool.ELLIPSE, "O")
        self.act_fill = mk_tool("Заливка (F)", Tool.FILL, "F")
        self.act_eye = mk_tool("Пипетка (I)", Tool.EYEDROPPER, "I")

        self.act_brush.setChecked(True)

    def _build_toolbar(self):
        tb = QToolBar("Инструменты", self)
        tb.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)

        tb.addAction(self.act_new)
        tb.addAction(self.act_open)
        tb.addAction(self.act_save)
        tb.addSeparator()

        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        tb.addSeparator()

        for a in [self.act_brush, self.act_eraser, self.act_line, self.act_rect, self.act_ellipse, self.act_fill, self.act_eye]:
            tb.addAction(a)

        tb.addSeparator()
        tb.addAction(self.act_copy)
        tb.addAction(self.act_paste)

    def _build_menus(self):
        m_file = self.menuBar().addMenu("Файл")
        m_file.addAction(self.act_new)
        m_file.addAction(self.act_open)
        m_file.addAction(self.act_save)
        m_file.addSeparator()
        m_file.addAction(self.act_export)

        m_edit = self.menuBar().addMenu("Правка")
        m_edit.addAction(self.act_undo)
        m_edit.addAction(self.act_redo)
        m_edit.addSeparator()
        m_edit.addAction(self.act_copy)
        m_edit.addAction(self.act_paste)

        m_tools = self.menuBar().addMenu("Инструменты")
        for a in [self.act_brush, self.act_eraser, self.act_line, self.act_rect, self.act_ellipse, self.act_fill, self.act_eye]:
            m_tools.addAction(a)

    def _update_undo_redo(self):
        self.act_undo.setEnabled(self.canvas.history.can_undo())
        self.act_redo.setEnabled(self.canvas.history.can_redo())

    def set_tool(self, tool: Tool):
        for t, a in self.tool_actions.items():
            a.setChecked(t == tool)
        self.canvas.set_tool(tool)
        self.statusBar().showMessage(f"Инструмент: {tool.name}", 1500)

    def undo(self):
        img = self.canvas.history.undo()
        if img is not None:
            self.canvas.set_image(img)
            self._update_undo_redo()

    def redo(self):
        img = self.canvas.history.redo()
        if img is not None:
            self.canvas.set_image(img)
            self._update_undo_redo()

    def new_file(self):
        # простой диалог через QMessageBox + значения по умолчанию
        # (чтобы код был в одном файле без отдельной формы)
        w, ok1 = self._get_int("Ширина", 1400, 64, 8000)
        if not ok1:
            return
        h, ok2 = self._get_int("Высота", 900, 64, 8000)
        if not ok2:
            return
        transparent = QMessageBox.question(
            self, "Фон", "Сделать фон прозрачным?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes
        self.canvas.new_image(w, h, transparent=transparent)
        self._update_undo_redo()

    def _get_int(self, title: str, default: int, mn: int, mx: int):
        # минималистичный ввод через QInputDialog-подобный подход без импорта QInputDialog
        # чтобы оставаться совместимыми с разными сборками Qt.
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"{title} ({mn}..{mx})"))
        edit = QLineEdit(str(default))
        lay.addWidget(edit)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        lay.addWidget(bb)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.Accepted:
            return default, False
        try:
            v = int(edit.text().strip())
            v = clamp(v, mn, mx)
            return v, True
        except Exception:
            return default, False

    def open_file(self):
        fn, _ = QFileDialog.getOpenFileName(
            self, "Открыть изображение", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;All Files (*.*)"
        )
        if not fn:
            return
        img = QImage(fn)
        if img.isNull():
            QMessageBox.warning(self, "Ошибка", "Не удалось открыть файл.")
            return
        self.canvas.set_image(img.convertToFormat(QImage.Format_ARGB32_Premultiplied))
        self.canvas.history.reset()
        self.canvas.history.push(self.canvas.image())
        self._update_undo_redo()
        self.statusBar().showMessage(f"Открыто: {fn}", 2500)

    def save_file(self):
        fn, _ = QFileDialog.getSaveFileName(
            self, "Сохранить как", "",
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;BMP (*.bmp)"
        )
        if not fn:
            return
        if not self.canvas.image().save(fn):
            QMessageBox.warning(self, "Ошибка", "Не удалось сохранить файл.")
            return
        self.statusBar().showMessage(f"Сохранено: {fn}", 2500)

    def export_png_transparent(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Экспорт PNG (прозрачность)", "", "PNG (*.png)")
        if not fn:
            return
        img = self.canvas.image()
        # как есть, с альфой
        if not img.save(fn, "PNG"):
            QMessageBox.warning(self, "Ошибка", "Не удалось экспортировать PNG.")
            return
        self.statusBar().showMessage(f"Экспортировано: {fn}", 2500)

    def copy_to_clipboard(self):
        cb = QGuiApplication.clipboard()
        cb.setImage(self.canvas.image())
        self.statusBar().showMessage("Скопировано в буфер обмена", 1500)

    def paste_from_clipboard(self):
        cb = QGuiApplication.clipboard()
        img = cb.image()
        if img.isNull():
            QMessageBox.information(self, "Вставка", "В буфере обмена нет изображения.")
            return
        self.canvas.set_image(img.convertToFormat(QImage.Format_ARGB32_Premultiplied))
        self.canvas.history.reset()
        self.canvas.history.push(self.canvas.image())
        self._update_undo_redo()
        self.statusBar().showMessage("Вставлено из буфера обмена", 1500)


def main():
    app = QApplication(sys.argv)
    force_light_theme(app)

    app.setApplicationName("AdultPaint")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()