import configparser
import os
import re
import sys
import threading
import time
from pathlib import Path

from PyQt5 import QtChart
from PyQt5.QtCore import QObject, QDateTime, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIntValidator, QPainter, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QProxyStyle,
    QSizePolicy,
    QStyle,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from playwright.sync_api import sync_playwright


if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = APP_DIR / "settings.ini"

DEFAULT_LOGIN_URL = "http://192.168.8.1/html/index.html"
DEFAULT_INFO_URL = "http://192.168.8.1/html/content.html#deviceinformation"
DEFAULT_INTERVAL_SECONDS = 2
MAX_HISTORY_POINTS = 120
SUPPORTED_THEMES = {"dark", "light"}
DEFAULT_THEME = "light"
TOOLTIP_WAKEUP_DELAY_MS = 250


class FastToolTipStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_ToolTip_WakeUpDelay:
            return TOOLTIP_WAKEUP_DELAY_MS
        return super().styleHint(hint, option, widget, returnData)


def load_settings():
    config = configparser.ConfigParser()
    config.read(SETTINGS_FILE, encoding="utf-8")

    connection = config["connection"] if config.has_section("connection") else {}
    runtime = config["runtime"] if config.has_section("runtime") else {}
    theme = os.getenv("RSRP_THEME", runtime.get("theme", DEFAULT_THEME)).lower()
    if theme not in SUPPORTED_THEMES:
        theme = DEFAULT_THEME

    return {
        "login_url": os.getenv("RSRP_LOGIN_URL", connection.get("login_url", DEFAULT_LOGIN_URL)),
        "info_url": os.getenv("RSRP_INFO_URL", connection.get("info_url", DEFAULT_INFO_URL)),
        "password": os.getenv("RSRP_MODEM_PASSWORD", connection.get("password", "")),
        "interval": int(os.getenv("RSRP_REFRESH_SECONDS", runtime.get("refresh_seconds", DEFAULT_INTERVAL_SECONDS))),
        "headless": os.getenv("RSRP_HEADLESS", runtime.get("headless", "true")).lower() not in {"0", "false", "no"},
        "theme": theme,
    }


def save_settings(login_url, info_url, interval, headless, password="", save_password=False, theme=DEFAULT_THEME):
    config = configparser.ConfigParser()
    config["connection"] = {
        "login_url": login_url,
        "info_url": info_url,
    }
    if save_password:
        config["connection"]["password"] = password

    config["runtime"] = {
        "refresh_seconds": str(interval),
        "headless": "true" if headless else "false",
        "theme": theme if theme in SUPPORTED_THEMES else DEFAULT_THEME,
    }

    with SETTINGS_FILE.open("w", encoding="utf-8") as file:
        config.write(file)


def save_theme_setting(theme):
    config = configparser.ConfigParser()
    config.read(SETTINGS_FILE, encoding="utf-8")
    if not config.has_section("runtime"):
        config.add_section("runtime")
    config.set("runtime", "theme", theme if theme in SUPPORTED_THEMES else DEFAULT_THEME)
    with SETTINGS_FILE.open("w", encoding="utf-8") as file:
        config.write(file)


def extract_number(value):
    if not value:
        return None
    match = re.search(r"-?\d+", value)
    return int(match.group()) if match else None


def get_rsrp_quality(value):
    rsrp = extract_number(value)
    if rsrp is None:
        return "No data", "neutral"
    if rsrp >= -70:
        return "Excellent", "excellent"
    if rsrp >= -76:
        return "Very good", "good"
    if rsrp >= -85:
        return "Good", "good"
    if rsrp >= -95:
        return "Fair", "warning"
    if rsrp >= -105:
        return "Weak", "danger"
    if rsrp >= -110:
        return "Marginal", "danger"
    return "No signal", "critical"


def get_sinr_quality(value):
    sinr = extract_number(value)
    if sinr is None:
        return "No data", "neutral"
    if sinr >= 25:
        return "Excellent", "excellent"
    if sinr >= 20:
        return "Very good", "good"
    if sinr >= 10:
        return "Good", "good"
    if sinr >= 5:
        return "Fair", "warning"
    if sinr >= 0:
        return "Noisy", "danger"
    if sinr >= -10:
        return "Heavy interference", "danger"
    return "No usable signal", "critical"


class MonitorSignals(QObject):
    data = pyqtSignal(dict)
    log = pyqtSignal(str)
    state = pyqtSignal(str)
    finished = pyqtSignal()


class MonitorWorker:
    def __init__(self, login_url, info_url, password, interval, headless, signals, stop_event):
        self.login_url = login_url
        self.info_url = info_url
        self.password = password
        self.interval = interval
        self.headless = headless
        self.signals = signals
        self.stop_event = stop_event

    def run(self):
        try:
            if not self.password:
                self.signals.log.emit("Password is missing. Enter it in the app, settings.ini, or RSRP_MODEM_PASSWORD.")
                return

            self.signals.state.emit("Connecting")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=self.headless)
                context = browser.new_context()
                page = context.new_page()

                try:
                    self.signals.log.emit(f"Opening login page: {self.login_url}")
                    page.goto(self.login_url, wait_until="domcontentloaded", timeout=30000)

                    self.signals.log.emit("Entering password and signing in")
                    page.wait_for_selector("#login_password", timeout=10000)
                    page.fill("#login_password", self.password)
                    page.press("#login_password", "Enter")
                    page.wait_for_load_state("networkidle", timeout=30000)

                    self.signals.log.emit(f"Opening signal information page: {self.info_url}")
                    page.goto(self.info_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)

                    self.signals.state.emit("Monitoring")
                    self.signals.log.emit("Monitoring started")

                    while not self.stop_event.is_set():
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(800)

                        rsrp, sinr = self.read_signal(page)
                        rsrp_quality, rsrp_level = get_rsrp_quality(rsrp)
                        sinr_quality, sinr_level = get_sinr_quality(sinr)

                        self.signals.data.emit(
                            {
                                "timestamp": QDateTime.currentDateTime(),
                                "rsrp": rsrp or "-",
                                "sinr": sinr or "-",
                                "rsrp_value": extract_number(rsrp),
                                "sinr_value": extract_number(sinr),
                                "rsrp_quality": rsrp_quality,
                                "sinr_quality": sinr_quality,
                                "rsrp_level": rsrp_level,
                                "sinr_level": sinr_level,
                            }
                        )
                        self.signals.log.emit(f"RSRP: {rsrp or '-'} ({rsrp_quality}), SINR: {sinr or '-'} ({sinr_quality})")
                        self.stop_event.wait(self.interval)
                finally:
                    context.close()
                    browser.close()

        except Exception as error:
            self.signals.log.emit(f"Monitoring error: {error}")
        finally:
            self.signals.state.emit("Stopped")
            self.signals.finished.emit()

    def read_signal(self, page):
        rsrp = self.read_first_text(
            page,
            [
                "#deviceinformation_rsrp",
                "#di-rsrp span",
            ],
        )
        sinr = self.read_first_text(
            page,
            [
                "#deviceinformation\\.sinr span.radio-box",
                "#di-sinr span",
            ],
        )
        return rsrp, sinr

    @staticmethod
    def read_first_text(page, selectors):
        for selector in selectors:
            try:
                page.wait_for_selector(selector, timeout=2500)
                element = page.query_selector(selector)
                if element:
                    text = element.inner_text().strip()
                    if text:
                        return text
            except Exception:
                continue
        return None


class MetricCard(QFrame):
    def __init__(self, title, unit, accent):
        super().__init__()
        self.setObjectName("metricCard")
        self.accent = accent

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")

        self.value_label = QLabel("-")
        self.value_label.setObjectName("metricValue")

        self.quality_label = QLabel("Waiting for data")
        self.quality_label.setObjectName("metricQuality")

        self.unit_label = QLabel(unit)
        self.unit_label.setObjectName("metricUnit")

        top_row = QHBoxLayout()
        top_row.addWidget(title_label)
        top_row.addStretch()
        top_row.addWidget(self.unit_label)

        layout.addLayout(top_row)
        layout.addWidget(self.value_label)
        layout.addWidget(self.quality_label)

    def update_metric(self, value, quality, level):
        self.value_label.setText(value)
        self.quality_label.setText(quality)
        self.setProperty("level", level)
        self.style().unpolish(self)
        self.style().polish(self)


class SignalMonitorApp(QMainWindow):
    LEVEL_COLORS = {
        "excellent": "#32d583",
        "good": "#7cd992",
        "warning": "#fdb022",
        "danger": "#ff7a45",
        "critical": "#f04438",
        "neutral": "#98a2b3",
    }

    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.monitoring_active = False
        self.worker_thread = None
        self.stop_event = threading.Event()
        self.signals = MonitorSignals()
        self.rsrp_history = []
        self.sinr_history = []
        self.max_rsrp = None
        self.min_sinr = None
        self.theme = self.settings["theme"]

        self.setWindowTitle("RSRP Checker")
        self.setMinimumSize(1380, 1000)
        self.resize(1380, 1000)

        self.build_ui()
        self.setup_chart()
        self.apply_theme()
        self.bind_signals()
        self.add_log("Application is ready. Configure the connection and press Start.")

    def build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(24, 22, 24, 22)
        root_layout.setSpacing(18)

        root_layout.addWidget(self.create_header())

        content = QHBoxLayout()
        content.setSpacing(18)
        root_layout.addLayout(content, 1)

        left_column = QVBoxLayout()
        left_column.setSpacing(18)
        content.addLayout(left_column, 0)

        left_column.addWidget(self.create_connection_card())
        left_column.addWidget(self.create_stats_card())
        left_column.addStretch()

        right_column = QVBoxLayout()
        right_column.setSpacing(18)
        content.addLayout(right_column, 1)

        right_column.addWidget(self.create_metrics_row())
        right_column.addWidget(self.create_chart_card(), 2)
        right_column.addWidget(self.create_log_card(), 1)

    def create_header(self):
        header = QFrame()
        header.setObjectName("hero")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(18)

        title_block = QVBoxLayout()
        title_block.setSpacing(6)

        eyebrow = QLabel("LTE signal monitor")
        eyebrow.setObjectName("eyebrow")

        title = QLabel("Signal Quality Monitor")
        title.setObjectName("appTitle")

        subtitle = QLabel("Track RSRP and SINR in real time.")
        subtitle.setObjectName("subtitle")

        title_block.addWidget(eyebrow)
        title_block.addWidget(title)
        title_block.addWidget(subtitle)

        status_block = QVBoxLayout()
        status_block.setSpacing(10)

        self.status_pill = QLabel("Stopped")
        self.status_pill.setObjectName("statusPill")

        self.theme_button = QToolButton()
        self.theme_button.setObjectName("themeButton")
        self.theme_button.setCheckable(True)
        self.theme_button.clicked.connect(self.toggle_theme)
        self.update_theme_button_text()

        status_block.addWidget(self.status_pill, 0, Qt.AlignRight)
        status_block.addWidget(self.theme_button, 0, Qt.AlignRight)

        layout.addLayout(title_block, 1)
        layout.addLayout(status_block, 0)
        return header

    def create_connection_card(self):
        card = self.create_card("Connection")
        layout = card.layout()

        self.login_url_input = self.create_input(self.settings["login_url"])
        self.info_url_input = self.create_input(self.settings["info_url"])
        self.password_input = self.create_input(self.settings["password"])
        self.password_input.setEchoMode(QLineEdit.Password)

        password_row = QHBoxLayout()
        password_row.setSpacing(8)
        password_row.addWidget(self.password_input, 1)

        self.toggle_password_button = QToolButton()
        self.toggle_password_button.setText("Show")
        self.toggle_password_button.setCheckable(True)
        self.toggle_password_button.clicked.connect(self.toggle_password_visibility)
        password_row.addWidget(self.toggle_password_button)

        self.interval_input = self.create_input(str(self.settings["interval"]))
        self.interval_input.setValidator(QIntValidator(1, 3600, self))

        self.headless_checkbox = QCheckBox("Run browser in the background")
        self.headless_checkbox.setChecked(self.settings["headless"])

        self.save_password_checkbox = QCheckBox("Save password in local settings.ini")
        password_tip_button = QToolButton()
        password_tip_button.setObjectName("tipButton")
        password_tip_button.setText("!")
        password_tip_button.setToolTip("Keep the password in the RSRP_MODEM_PASSWORD environment variable for better privacy.")
        password_tip_button.setToolTipDuration(8000)
        password_tip_button.setFixedSize(28, 28)

        password_options = QHBoxLayout()
        password_options.setSpacing(8)
        password_options.addWidget(self.save_password_checkbox)
        password_options.addWidget(password_tip_button)
        password_options.addStretch()

        layout.addWidget(self.create_field("Login page", self.login_url_input))
        layout.addWidget(self.create_field("Signal data page", self.info_url_input))
        layout.addWidget(self.create_field("Router password", password_row))
        layout.addWidget(self.create_field("Refresh interval, sec", self.interval_input))
        layout.addWidget(self.headless_checkbox)
        layout.addLayout(password_options)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.start_monitoring)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("secondaryButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_monitoring)

        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("ghostButton")
        self.save_button.clicked.connect(self.save_current_settings)

        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.save_button)
        layout.addLayout(controls)

        return card

    def create_stats_card(self):
        card = self.create_card("Session")
        layout = card.layout()

        self.max_rsrp_label = QLabel("-")
        self.max_rsrp_label.setObjectName("statValue")
        self.min_sinr_label = QLabel("-")
        self.min_sinr_label.setObjectName("statValue")
        self.samples_label = QLabel("0")
        self.samples_label.setObjectName("statValue")

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(14)
        grid.addWidget(self.create_stat("Best RSRP", self.max_rsrp_label), 0, 0)
        grid.addWidget(self.create_stat("Minimum SINR", self.min_sinr_label), 0, 1)
        grid.addWidget(self.create_stat("Samples", self.samples_label), 1, 0, 1, 2)

        layout.addLayout(grid)
        return card

    def create_metrics_row(self):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        self.rsrp_card = MetricCard("RSRP", "dBm", "#32d583")
        self.sinr_card = MetricCard("SINR", "dB", "#2e90fa")

        layout.addWidget(self.rsrp_card)
        layout.addWidget(self.sinr_card)
        return row

    def create_chart_card(self):
        card = self.create_card("Signal Trend")
        layout = card.layout()

        self.chart = QtChart.QChart()
        self.chart.setAnimationOptions(QtChart.QChart.SeriesAnimations)
        self.chart.legend().setVisible(True)
        self.chart.legend().setAlignment(Qt.AlignBottom)

        self.rsrp_series = QtChart.QLineSeries()
        self.rsrp_series.setName("RSRP")
        self.sinr_series = QtChart.QLineSeries()
        self.sinr_series.setName("SINR")

        self.chart.addSeries(self.rsrp_series)
        self.chart.addSeries(self.sinr_series)

        self.axis_x = QtChart.QDateTimeAxis()
        self.axis_x.setFormat("HH:mm:ss")
        self.axis_x.setTitleText("Time")

        self.axis_y_rsrp = QtChart.QValueAxis()
        self.axis_y_rsrp.setTitleText("RSRP, dBm")
        self.axis_y_rsrp.setRange(-115, -60)
        self.axis_y_rsrp.setLabelFormat("%d")

        self.axis_y_sinr = QtChart.QValueAxis()
        self.axis_y_sinr.setTitleText("SINR, dB")
        self.axis_y_sinr.setRange(-12, 35)
        self.axis_y_sinr.setLabelFormat("%d")

        self.chart.addAxis(self.axis_x, Qt.AlignBottom)
        self.chart.addAxis(self.axis_y_rsrp, Qt.AlignLeft)
        self.chart.addAxis(self.axis_y_sinr, Qt.AlignRight)

        self.rsrp_series.attachAxis(self.axis_x)
        self.rsrp_series.attachAxis(self.axis_y_rsrp)
        self.sinr_series.attachAxis(self.axis_x)
        self.sinr_series.attachAxis(self.axis_y_sinr)

        self.chart_view = QtChart.QChartView(self.chart)
        self.chart_view.setRenderHint(QPainter.Antialiasing)
        self.chart_view.setMinimumHeight(320)
        self.chart_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout.addWidget(self.chart_view)
        return card

    def create_log_card(self):
        card = self.create_card("Log")
        layout = card.layout()

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(150)
        layout.addWidget(self.log_view)
        return card

    def create_card(self, title):
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        layout.addWidget(title_label)
        return card

    @staticmethod
    def create_input(value):
        field = QLineEdit(value)
        field.setMinimumHeight(42)
        return field

    @staticmethod
    def create_field(label_text, widget_or_layout):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel(label_text)
        label.setObjectName("fieldLabel")
        layout.addWidget(label)

        if isinstance(widget_or_layout, QHBoxLayout):
            layout.addLayout(widget_or_layout)
        else:
            layout.addWidget(widget_or_layout)
        return container

    @staticmethod
    def create_stat(title, value_label):
        frame = QFrame()
        frame.setObjectName("statBox")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title_label = QLabel(title)
        title_label.setObjectName("statTitle")
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return frame

    def setup_chart(self):
        rsrp_pen = self.rsrp_series.pen()
        rsrp_pen.setColor(QColor("#32d583"))
        rsrp_pen.setWidth(3)
        self.rsrp_series.setPen(rsrp_pen)

        sinr_pen = self.sinr_series.pen()
        sinr_pen.setColor(QColor("#2e90fa"))
        sinr_pen.setWidth(3)
        self.sinr_series.setPen(sinr_pen)

        now = QDateTime.currentDateTime()
        self.axis_x.setRange(now.addSecs(-60), now)

    def apply_theme(self):
        app = QApplication.instance()
        app.setStyle(FastToolTipStyle("Fusion"))
        palette = QPalette()
        if self.theme == "light":
            palette.setColor(QPalette.Window, QColor("#f6f8fb"))
            palette.setColor(QPalette.WindowText, QColor("#172033"))
            palette.setColor(QPalette.Base, QColor("#ffffff"))
            palette.setColor(QPalette.Text, QColor("#172033"))
            palette.setColor(QPalette.Button, QColor("#eef4fb"))
            palette.setColor(QPalette.ButtonText, QColor("#172033"))
            palette.setColor(QPalette.Highlight, QColor("#1479d4"))
        else:
            palette.setColor(QPalette.Window, QColor("#08111f"))
            palette.setColor(QPalette.WindowText, QColor("#e6edf7"))
            palette.setColor(QPalette.Base, QColor("#0f1b2d"))
            palette.setColor(QPalette.Text, QColor("#e6edf7"))
            palette.setColor(QPalette.Button, QColor("#17243a"))
            palette.setColor(QPalette.ButtonText, QColor("#e6edf7"))
            palette.setColor(QPalette.Highlight, QColor("#2e90fa"))
        app.setPalette(palette)

        if self.theme == "light":
            app.setStyleSheet(
                """
                QToolTip {
                    background: #ffffff;
                    border: 1px solid #cfdceb;
                    border-radius: 10px;
                    color: #23344a;
                    font-family: "Segoe UI", "Manrope", sans-serif;
                    font-size: 12px;
                    font-weight: 600;
                    padding: 8px 10px;
                }
                """
            )
            self.setStyleSheet(
                """
            QWidget#root {
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #f7fafc, stop: 0.5 #edf4fb, stop: 1 #eaf7f0);
                color: #172033;
                font-family: "Segoe UI", "Manrope", sans-serif;
                font-size: 13px;
            }
            QFrame#hero {
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #ffffff, stop: 0.62 #f1f8ff, stop: 1 #edf8f0);
                border: 1px solid #d8e2ee;
                border-radius: 24px;
            }
            QLabel#eyebrow {
                color: #1479d4;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 1.5px;
                text-transform: uppercase;
            }
            QLabel#appTitle {
                color: #0f172a;
                font-size: 30px;
                font-weight: 800;
            }
            QLabel#subtitle {
                color: #52657a;
            }
            QLabel#statusPill {
                background: #e7f1ff;
                border: 1px solid #bdd7f5;
                border-radius: 16px;
                color: #155a9f;
                padding: 8px 14px;
                font-weight: 700;
            }
            QFrame#card, QFrame#metricCard {
                background: #ffffff;
                border: 1px solid #d9e3ef;
                border-radius: 20px;
            }
            QFrame#metricCard[level="excellent"], QFrame#metricCard[level="good"] {
                border-color: rgba(36, 167, 106, 0.50);
            }
            QFrame#metricCard[level="warning"] {
                border-color: rgba(217, 119, 6, 0.48);
            }
            QFrame#metricCard[level="danger"], QFrame#metricCard[level="critical"] {
                border-color: rgba(220, 38, 38, 0.48);
            }
            QLabel#cardTitle {
                color: #172033;
                font-size: 17px;
                font-weight: 800;
            }
            QLabel#fieldLabel, QLabel#statTitle, QLabel#metricTitle, QLabel#metricUnit {
                color: #617187;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel#metricValue {
                color: #111827;
                font-size: 42px;
                font-weight: 850;
            }
            QLabel#metricQuality {
                color: #52657a;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#statValue {
                color: #111827;
                font-size: 22px;
                font-weight: 800;
            }
            QFrame#statBox {
                background: #f4f7fb;
                border: 1px solid #dfe7f0;
                border-radius: 14px;
            }
            QLineEdit {
                background: #ffffff;
                border: 1px solid #cdd8e6;
                border-radius: 12px;
                color: #172033;
                padding: 8px 12px;
                selection-background-color: #1479d4;
                selection-color: #ffffff;
            }
            QLineEdit:focus {
                border: 1px solid #1479d4;
                background: #fbfdff;
            }
            QCheckBox {
                color: #34445a;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 5px;
                border: 1px solid #bbc8d8;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #24a76a;
                border-color: #24a76a;
            }
            QPushButton, QToolButton {
                border: none;
                border-radius: 12px;
                color: #172033;
                font-weight: 800;
                min-height: 42px;
                padding: 8px 14px;
            }
            QPushButton#primaryButton {
                background: #24a76a;
                color: #ffffff;
            }
            QPushButton#secondaryButton {
                background: #dc2626;
                color: #fff5f5;
            }
            QPushButton#ghostButton, QToolButton {
                background: #eef4fb;
                color: #23344a;
            }
            QToolButton#themeButton {
                border: 1px solid #d1deec;
            }
            QToolButton#tipButton {
                background: #eef4fb;
                border: 1px solid #cfdceb;
                border-radius: 14px;
                color: #52657a;
                font-size: 14px;
                font-weight: 900;
                min-height: 28px;
                min-width: 28px;
                padding: 0;
            }
            QPushButton:disabled {
                background: #e6ebf1;
                color: #94a3b8;
            }
            QTextEdit {
                background: #fbfdff;
                border: 1px solid #d6e1ee;
                border-radius: 14px;
                color: #28364a;
                font-family: "Cascadia Mono", "Consolas", monospace;
                font-size: 12px;
                padding: 10px;
            }
                """
            )

            self.chart.setBackgroundBrush(QColor("#ffffff"))
            self.chart.setPlotAreaBackgroundBrush(QColor("#f7fafc"))
            self.chart.setTitleBrush(QColor("#172033"))
            self.chart.legend().setLabelColor(QColor("#34445a"))
            axis_label_color = QColor("#52657a")
            grid_color = QColor("#dbe5f0")
            axis_line_color = QColor("#b9c8d8")
        else:
            app.setStyleSheet(
                """
                QToolTip {
                    background: #0d192b;
                    border: 1px solid rgba(125, 211, 252, 0.28);
                    border-radius: 10px;
                    color: #d8e6f6;
                    font-family: "Segoe UI", "Manrope", sans-serif;
                    font-size: 12px;
                    font-weight: 600;
                    padding: 8px 10px;
                }
                """
            )
            self.setStyleSheet(
                """
            QWidget#root {
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #07111f, stop: 0.48 #0b1c30, stop: 1 #102b2c);
                color: #e6edf7;
                font-family: "Segoe UI", "Manrope", sans-serif;
                font-size: 13px;
            }
            QFrame#hero {
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #12233b, stop: 0.6 #123047, stop: 1 #123a33);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 24px;
            }
            QLabel#eyebrow {
                color: #7dd3fc;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 1.5px;
                text-transform: uppercase;
            }
            QLabel#appTitle {
                color: #f8fbff;
                font-size: 30px;
                font-weight: 800;
            }
            QLabel#subtitle {
                color: #a9b7ca;
            }
            QLabel#statusPill {
                background: rgba(46, 144, 250, 0.16);
                border: 1px solid rgba(125, 211, 252, 0.35);
                border-radius: 16px;
                color: #bae6fd;
                padding: 8px 14px;
                font-weight: 700;
            }
            QFrame#card, QFrame#metricCard {
                background: rgba(13, 25, 43, 0.92);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 20px;
            }
            QFrame#metricCard[level="excellent"], QFrame#metricCard[level="good"] {
                border-color: rgba(50, 213, 131, 0.42);
            }
            QFrame#metricCard[level="warning"] {
                border-color: rgba(253, 176, 34, 0.48);
            }
            QFrame#metricCard[level="danger"], QFrame#metricCard[level="critical"] {
                border-color: rgba(240, 68, 56, 0.48);
            }
            QLabel#cardTitle {
                color: #f8fbff;
                font-size: 17px;
                font-weight: 800;
            }
            QLabel#fieldLabel, QLabel#statTitle, QLabel#metricTitle, QLabel#metricUnit {
                color: #9fb0c7;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel#metricValue {
                color: #f8fbff;
                font-size: 42px;
                font-weight: 850;
            }
            QLabel#metricQuality {
                color: #b6c7db;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#statValue {
                color: #f8fbff;
                font-size: 22px;
                font-weight: 800;
            }
            QFrame#statBox {
                background: rgba(255, 255, 255, 0.045);
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 14px;
            }
            QLineEdit {
                background: #0a1627;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 12px;
                color: #edf4ff;
                padding: 8px 12px;
                selection-background-color: #2e90fa;
            }
            QLineEdit:focus {
                border: 1px solid #38bdf8;
                background: #0d1b2e;
            }
            QCheckBox {
                color: #c7d4e5;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 5px;
                border: 1px solid rgba(255, 255, 255, 0.22);
                background: #0a1627;
            }
            QCheckBox::indicator:checked {
                background: #32d583;
                border-color: #32d583;
            }
            QPushButton, QToolButton {
                border: none;
                border-radius: 12px;
                color: #f8fbff;
                font-weight: 800;
                min-height: 42px;
                padding: 8px 14px;
            }
            QPushButton#primaryButton {
                background: #32d583;
                color: #062012;
            }
            QPushButton#secondaryButton {
                background: #f04438;
                color: #fff5f5;
            }
            QPushButton#ghostButton, QToolButton {
                background: rgba(255, 255, 255, 0.08);
                color: #d8e6f6;
            }
            QToolButton#themeButton {
                border: 1px solid rgba(255, 255, 255, 0.10);
            }
            QToolButton#tipButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 14px;
                color: #c7d4e5;
                font-size: 14px;
                font-weight: 900;
                min-height: 28px;
                min-width: 28px;
                padding: 0;
            }
            QPushButton:disabled {
                background: rgba(255, 255, 255, 0.07);
                color: #75849a;
            }
            QTextEdit {
                background: #07111f;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 14px;
                color: #c9d7e8;
                font-family: "Cascadia Mono", "Consolas", monospace;
                font-size: 12px;
                padding: 10px;
            }
                """
            )

            self.chart.setBackgroundBrush(QColor("#0a1627"))
            self.chart.setPlotAreaBackgroundBrush(QColor("#08111f"))
            self.chart.setTitleBrush(QColor("#e6edf7"))
            self.chart.legend().setLabelColor(QColor("#c7d4e5"))
            axis_label_color = QColor("#a9b7ca")
            grid_color = QColor("#1f334d")
            axis_line_color = QColor("#35506f")

        self.chart.setPlotAreaBackgroundVisible(True)
        self.chart.legend().setFont(QFont("Segoe UI", 9))

        for axis in [self.axis_x, self.axis_y_rsrp, self.axis_y_sinr]:
            axis.setLabelsColor(axis_label_color)
            axis.setTitleBrush(axis_label_color)
            axis.setGridLineColor(grid_color)
            axis.setLinePenColor(axis_line_color)

    def bind_signals(self):
        self.signals.data.connect(self.handle_data)
        self.signals.log.connect(self.add_log)
        self.signals.state.connect(self.set_state)
        self.signals.finished.connect(self.handle_worker_finished)

    def toggle_password_visibility(self):
        visible = self.toggle_password_button.isChecked()
        self.password_input.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        self.toggle_password_button.setText("Hide" if visible else "Show")

    def update_theme_button_text(self):
        self.theme_button.blockSignals(True)
        self.theme_button.setChecked(self.theme == "light")
        self.theme_button.setText("Light theme" if self.theme == "light" else "Dark theme")
        self.theme_button.blockSignals(False)

    def toggle_theme(self, checked=None):
        self.theme = "light" if self.theme_button.isChecked() else "dark"
        self.update_theme_button_text()
        self.apply_theme()
        save_theme_setting(self.theme)
        self.add_log(f"Theme switched to {self.theme}.")

    def save_current_settings(self):
        login_url, info_url, password, interval, headless = self.collect_settings()
        save_settings(
            login_url,
            info_url,
            interval,
            headless,
            password=password,
            save_password=self.save_password_checkbox.isChecked(),
            theme=self.theme,
        )
        if self.save_password_checkbox.isChecked():
            self.add_log("Settings saved to settings.ini.")
        else:
            self.add_log("Settings saved without the password. Use the input field or RSRP_MODEM_PASSWORD for the password.")

    def collect_settings(self):
        login_url = self.login_url_input.text().strip() or DEFAULT_LOGIN_URL
        info_url = self.info_url_input.text().strip() or DEFAULT_INFO_URL
        password = self.password_input.text()
        interval = int(self.interval_input.text() or DEFAULT_INTERVAL_SECONDS)
        headless = self.headless_checkbox.isChecked()
        return login_url, info_url, password, max(1, interval), headless

    def start_monitoring(self):
        if self.monitoring_active:
            return

        login_url, info_url, password, interval, headless = self.collect_settings()
        self.reset_session()
        self.save_current_settings()

        self.monitoring_active = True
        self.stop_event.clear()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.set_state("Starting")

        worker = MonitorWorker(login_url, info_url, password, interval, headless, self.signals, self.stop_event)
        self.worker_thread = threading.Thread(target=worker.run, daemon=True)
        self.worker_thread.start()

    def stop_monitoring(self):
        if not self.monitoring_active:
            return
        self.add_log("Stopping monitoring...")
        self.stop_event.set()
        self.set_state("Stopping")

    def reset_session(self):
        self.rsrp_history.clear()
        self.sinr_history.clear()
        self.max_rsrp = None
        self.min_sinr = None
        self.max_rsrp_label.setText("-")
        self.min_sinr_label.setText("-")
        self.samples_label.setText("0")
        self.rsrp_series.clear()
        self.sinr_series.clear()
        self.rsrp_card.update_metric("-", "Waiting for data", "neutral")
        self.sinr_card.update_metric("-", "Waiting for data", "neutral")

    def handle_data(self, data):
        self.rsrp_card.update_metric(data["rsrp"], data["rsrp_quality"], data["rsrp_level"])
        self.sinr_card.update_metric(data["sinr"], data["sinr_quality"], data["sinr_level"])

        timestamp = data["timestamp"]
        rsrp_value = data["rsrp_value"]
        sinr_value = data["sinr_value"]

        if rsrp_value is not None:
            self.rsrp_history.append((timestamp, rsrp_value))
            self.rsrp_history = self.rsrp_history[-MAX_HISTORY_POINTS:]
            if self.max_rsrp is None or rsrp_value > self.max_rsrp:
                self.max_rsrp = rsrp_value
                self.max_rsrp_label.setText(f"{rsrp_value} dBm")

        if sinr_value is not None:
            self.sinr_history.append((timestamp, sinr_value))
            self.sinr_history = self.sinr_history[-MAX_HISTORY_POINTS:]
            if self.min_sinr is None or sinr_value < self.min_sinr:
                self.min_sinr = sinr_value
                self.min_sinr_label.setText(f"{sinr_value} dB")

        self.samples_label.setText(str(max(len(self.rsrp_history), len(self.sinr_history))))
        self.update_chart()

    def update_chart(self):
        self.rsrp_series.clear()
        self.sinr_series.clear()

        for timestamp, value in self.rsrp_history:
            self.rsrp_series.append(timestamp.toMSecsSinceEpoch(), value)
        for timestamp, value in self.sinr_history:
            self.sinr_series.append(timestamp.toMSecsSinceEpoch(), value)

        all_points = self.rsrp_history + self.sinr_history
        if all_points:
            first = min(point[0] for point in all_points)
            last = max(point[0] for point in all_points)
            self.axis_x.setRange(first.addSecs(-5), last.addSecs(5))

    def add_log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_view.append(f"[{timestamp}] {message}")
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def set_state(self, state):
        self.status_pill.setText(state)

    def handle_worker_finished(self):
        self.monitoring_active = False
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.stop_event.set()
        self.add_log("Monitoring stopped")

    def closeEvent(self, event):
        self.stop_event.set()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SignalMonitorApp()
    window.show()
    sys.exit(app.exec_())
