from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QMainWindow, QVBoxLayout, QWidget
from PyQt6.QtCore import QUrl

class OrbitBrowser(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Orbit Safe Browser")
        self.resize(1024, 768)
        
        # Web View
        self.browser = QWebEngineView()
        self.browser.setUrl(QUrl("https://www.google.com"))
        
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.browser)
        self.setCentralWidget(container)

    def navigate(self, url: str):
        if not url.startswith("http"):
            url = "https://" + url
        self.browser.setUrl(QUrl(url))
        self.show()
