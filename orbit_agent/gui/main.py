import sys
import asyncio
import qasync
import os
import tempfile
from datetime import datetime
from uuid import UUID

# Force UTF-8 on Windows to prevent Emoji crashes
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Fix Windows Console Encoding for Unicode (Emojis/Arrows)
try:
    if sys.stdout: sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr: sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    pass

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, 
                             QLabel, QSystemTrayIcon, QMenu, QGraphicsDropShadowEffect, QTextBrowser, QFrame, QSizePolicy, QPushButton)
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QSize, QPropertyAnimation, QEasingCurve, QRect, QTimer, QEvent
from PyQt6.QtGui import QColor, QPainter, QBrush, QIcon, QFont, QCursor, QPen, QRadialGradient

from orbit_agent.config.config import OrbitConfig
from orbit_agent.core.agent import Agent
from orbit_agent.core.voice import VoiceEngine
from orbit_agent.tasks.models import TaskState

# --- THEME: ORBIT INTELLIGENCE (Glass) ---
COLOR_BG = "rgba(9, 9, 11, 0.95)"      # Zinc 950 (High Opacity)
COLOR_BORDER = "rgba(255, 255, 255, 0.1)"
COLOR_ACCENT = "#3b82f6"               # Blue 500
COLOR_TEXT = "#fafafa"
COLOR_INPUT_BG = "rgba(39, 39, 42, 0.5)" # Zinc 800 (Sheer)
FONT_FAMILY = "Segoe UI"

CSS_MAIN = f"""
    QWidget {{
        font-family: '{FONT_FAMILY}', sans-serif;
        font-size: 13px;
        color: {COLOR_TEXT};
    }}
    /* Main Card Container */
    QFrame#MainCard {{
        background-color: {COLOR_BG};
        border: 1px solid {COLOR_BORDER};
        border-radius: 16px;
    }}
    /* Chat Area */
    QTextBrowser {{
        background-color: transparent;
        border: none;
        selection-background-color: {COLOR_ACCENT};
    }}
    /* Input Area */
    QLineEdit {{
        background-color: {COLOR_INPUT_BG};
        color: {COLOR_TEXT};
        border: 1px solid transparent;
        border-radius: 12px;
        padding: 12px 14px;
        padding-right: 40px; /* Space for eye icon */
        font-size: 14px;
    }}
    QLineEdit:focus {{
        border: 1px solid {COLOR_ACCENT};
        background-color: rgba(39, 39, 42, 0.8);
    }}
    /* Vision Button (Inside Input) */
    QPushButton#VisionBtn {{
        background-color: transparent;
        border: none;
        border-radius: 10px;
        font-size: 16px;
    }}
    QPushButton#VisionBtn:hover {{
        background-color: rgba(255,255,255,0.1);
    }}
"""

CSS_SCROLLBAR = """
    QScrollBar:vertical { width: 6px; background: transparent; margin: 0px; }
    QScrollBar::handle:vertical { background: rgba(255, 255, 255, 0.15); min-height: 20px; border-radius: 3px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
"""

class GlowingAvatar(QWidget):
    clicked = pyqtSignal()
    def __init__(self1, parent=None):
        super().__init__(parent)
        self1.setFixedSize(70, 70)
        self1.state_color = QColor(COLOR_ACCENT)
        self1.pulse_phase = 0
        self1.is_dragging = False
        self1.old_pos = None
        self1.timer = QTimer(self1)
        self1.timer.timeout.connect(self1._animate_pulse)
        self1.timer.start(50)

    def _animate_pulse(self1):
        self1.pulse_phase += 0.1
        self1.update()

    def set_state_color(self1, hex_color):
        self1.state_color = QColor(hex_color)
        self1.update()

    def paintEvent(self1, event):
        painter = QPainter(self1)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        import math
        pulse = (math.sin(self1.pulse_phase) + 1) / 2
        glow_size = 60 + (pulse * 4) # Subtle glow
        
        # Glow
        if self1.state_color.name() == COLOR_ACCENT:
            gradient = QRadialGradient(35, 35, 35)
            gradient.setColorAt(0, self1.state_color)
            gradient.setColorAt(1, Qt.GlobalColor.transparent)
            painter.setBrush(QBrush(gradient))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPoint(35, 35), int(glow_size/2), int(glow_size/2))

        # Core
        painter.setBrush(QBrush(self1.state_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(5, 5, 60, 60)
        
        # Minimalist Eyes (Dots)
        painter.setBrush(QBrush(QColor("white")))
        painter.drawEllipse(25, 30, 4, 4) 
        painter.drawEllipse(41, 30, 4, 4)

    # ... Mouse events
    def mousePressEvent(self1, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self1.old_pos = event.globalPosition().toPoint()
            self1.is_dragging = False
        elif event.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self1)
            menu.setStyleSheet(f"QMenu {{ background-color: {COLOR_BG}; color: white; border: 1px solid {COLOR_BORDER}; }}")
            menu.addAction("Reset Position").triggered.connect(lambda: self1.parent().move(100, 100))
            menu.addAction("Quit Orbit").triggered.connect(QApplication.instance().quit)
            menu.exec(event.globalPosition().toPoint())
            
    def mouseMoveEvent(self1, event):
        if self1.old_pos:
            delta = event.globalPosition().toPoint() - self1.old_pos
            if delta.manhattanLength() > 5:
                self1.is_dragging = True
                self1.parent().move(self1.parent().pos() + delta)
                self1.old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self1, event):
        if not self1.is_dragging and event.button() == Qt.MouseButton.LeftButton:
            self1.clicked.emit()
        self1.old_pos = None


class MainCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MainCard")
        # Drop Shadow for the Card
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40); shadow.setColor(QColor(0,0,0,120)); shadow.setOffset(0, 8)
        self.setGraphicsEffect(shadow)
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        
        # 1. Header (Draggable ideally, but for now simple spacer or status)
        self.header = QLabel("Orbit") # Hidden or Minimal
        self.header.setStyleSheet(f"color: #52525b; font-weight: bold; padding: 12px 16px 4px 16px; font-size: 10px; text-transform: uppercase; letter-spacing: 1px;")
        self.layout.addWidget(self.header)

        # 2. Chat History
        self.history = QTextBrowser()
        self.history.setOpenExternalLinks(True)
        self.history.verticalScrollBar().setStyleSheet(CSS_SCROLLBAR)
        self.layout.addWidget(self.history)
        
        # 3. Footer (Input Overlay)
        self.footer = QWidget()
        self.footer_layout = QHBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(12, 8, 12, 12)
        
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Ask me anything...")
        
        # Overlay Vision Button inside Input? 
        # Easier to just place it seamlessly next to it or stack?
        # Let's use a layout for the input row
        
        self.snap_btn = QPushButton("👁️")
        self.snap_btn.setObjectName("VisionBtn")
        self.snap_btn.setFixedSize(32, 32)
        self.snap_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.footer_layout.addWidget(self.input_field)
        self.footer_layout.addWidget(self.snap_btn)
        
        self.layout.addWidget(self.footer)

    def append_message(self, text, is_user=False):
        import html, re
        text = html.escape(text)
        text = text.replace("\n", "<br>")
        text = re.sub(r'`(.*?)`', r'<code style="background: rgba(255,255,255,0.1); padding: 2px 4px; border-radius: 4px;">\1</code>', text)
        
        font = FONT_FAMILY
        
        if is_user:
            html = f"""
            <div style="text-align: right; margin: 8px 12px;">
                <div style="display: inline-block; background-color: #27272a; color: #fafafa; 
                            padding: 8px 12px; border-radius: 12px; border-bottom-right-radius: 2px; text-align: left; font-family: {font};">
                    {text}
                </div>
            </div>
            """
        else:
            html = f"""
            <div style="text-align: left; margin: 8px 12px;">
                <div style="color: #fafafa; font-family: {font}; line-height: 1.4;">
                    <span style="color: {COLOR_ACCENT}; font-weight: bold; font-size: 10px; text-transform: uppercase;">Orbit</span><br>
                    {text}
                </div>
            </div>
            """
        self.history.append(html)
        self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

class OrbitWidget(QWidget):
    def __init__(self, agent):
        super().__init__()
        self.agent = agent
        self.current_screenshot = None
        self.is_expanded = False
        
        # Window Props
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(CSS_MAIN)
        
        # Main Layout (Stack: Card -> Gap -> Avatar)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(10, 10, 10, 10) # Padding for shadows
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        
        # Card
        self.card = MainCard()
        self.card.hide()
        # Wire up inputs
        self.card.input_field.returnPressed.connect(self.on_submit)
        self.card.snap_btn.clicked.connect(self.take_screenshot)
        
        self.main_layout.addWidget(self.card)
        
        self.main_layout.addSpacing(12)
        
        # Avatar Row
        self.avatar_row = QHBoxLayout()
        self.avatar_row.addStretch()
        self.avatar = GlowingAvatar(self)
        self.avatar.clicked.connect(self.toggle_expanded)
        self.avatar_row.addWidget(self.avatar)
        
        self.main_layout.addLayout(self.avatar_row)
        
        # Init Geometry
        screen = QApplication.primaryScreen().geometry()
        self.resize(400, 600)
        self.move(screen.width() - 420, screen.height() - 650)
        self.card.hide()
    
    def toggle_expanded(self):
        # ...Logic to show/hide self.card...
        screen = QApplication.primaryScreen().geometry()
        if self.is_expanded:
            self.card.hide()
            self.is_expanded = False
            self.resize(100, 100) # Shrink to fit avatar
        else:
            self.resize(400, 600)
            self.card.show()
            self.card.input_field.setFocus()
            self.is_expanded = True
            
            if not self.card.history.toPlainText():
                self.card.append_message("System Online. Ready.", is_user=False)
    
    def on_submit(self):
        text = self.card.input_field.text().strip()
        if not text: return
        
        if text.lower() in ["/quit", "/exit"]:
            QApplication.instance().quit()
            return

        img_path = self.current_screenshot
        self.card.append_message(text, is_user=True)
        self.card.input_field.clear()
        
        if img_path:
            self.current_screenshot = None
            self.card.input_field.setPlaceholderText("Message Orbit...")
            self.card.input_field.setStyleSheet("")
            print(f"[GUI] Vision Request: {img_path}")
            self.avatar.set_state_color("#89b4fa")
            asyncio.create_task(self.run_chat(text, img_path))
            return

        # Intent
        task_keywords = ["create", "build", "write", "open", "close", "search", "find", "check"]
        if any(k in text.lower() for k in task_keywords) or len(text) > 50:
             self.avatar.set_state_color("#fab387")
             asyncio.create_task(self.run_task(text))
        else:
             self.avatar.set_state_color("#89b4fa")
             asyncio.create_task(self.run_chat(text))

    async def run_chat(self, msg, image_path=None):
        try:
             reply = await self.agent.chat(msg, image_path)
             self.card.append_message(reply)
        except Exception as e:
             self.card.append_message(f"Error: {e}")

    async def run_task(self, goal):
        try:
            self.card.append_message(f"Initializing: {goal[:20]}...")
            task = await self.agent.create_task(goal)
            await self.agent.run_loop(task.id)
            
            self.card.append_message("Analyzing results...")
            report = await self.agent.chat(
                f"SYSTEM: Task '{goal}' Complete. "
                "List created files and final outcome ONLY. "
                "Be extremely brief (bullet points)."
            )
            
            self.card.append_message(report)
            
            self.avatar.set_state_color("#a6e3a1") # Green
            await asyncio.sleep(2)
            self.avatar.set_state_color(COLOR_ACCENT) # Back to Blue
            
        except Exception as e:
            self.card.append_message(f"Task Failed: {e}")
            self.avatar.set_state_color("#f38ba8") # Red

    def take_screenshot(self):
        print("Snapshot requested...")
        self.hide()
        QTimer.singleShot(300, self._capture_core)

    def _capture_core(self):
        try:
            print("Capturing screenshot...", flush=True)
            screenshot_dir = os.path.join(os.getcwd(), "screenshots")
            os.makedirs(screenshot_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(screenshot_dir, f"vision_{timestamp}.png")

            # Use PyAutoGUI screenshot (more reliable for GPU-accelerated windows than MSS on Windows)
            import pyautogui
            pyautogui.screenshot(filepath)
            
            print(f"Snapshot saved: {filepath}", flush=True)
            self.current_screenshot = filepath
            
            self.card.input_field.setPlaceholderText(f"Snapshot Captured!")
            self.card.input_field.setStyleSheet(f"border: 1px solid #a6e3a1;")
            self.card.input_field.setFocus()
            self.card.append_message("Snapshot captured. Ask a question about it.")
            
        except Exception as e:
            print(f"Snapshot Error: {e}", flush=True)
            self.card.append_message(f"Snapshot failed: {e}")
        finally:
            self.show()
            self.activateWindow()

async def start_orbit_gui(app):
    print("Initializing Orbit Agent...")
    config = OrbitConfig.load()
    config.safe_mode = False # Disable Safe Mode for autonomous GUI operation
    
    agent = Agent(config, interactive=False)
    
    global widget 
    widget = OrbitWidget(agent)
    widget.show()
    
    # Tray
    global tray
    tray = QSystemTrayIcon(QIcon("icon.png"), app) 
    menu = QMenu()
    show_action = menu.addAction("Show Orbit")
    show_action.triggered.connect(widget.show)
    quit_action = menu.addAction("Quit Orbit")
    quit_action.triggered.connect(QApplication.instance().quit)
    tray.setContextMenu(menu)
    tray.show()
    
    def on_tray_activate(reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if widget.isVisible(): widget.hide()
            else: widget.show()
    tray.activated.connect(on_tray_activate)
    
    print("Orbit GUI (Professional Theme) Running.")

if __name__ == "__main__":
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        loop.run_until_complete(start_orbit_gui(app))
        loop.run_forever()
