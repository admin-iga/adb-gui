import io
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6.QtCore import (
    QObject,
    QSize,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QFont, QImage, QPixmap, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)


# ----------------------------------------------------------------------------
# ADB plumbing
# ----------------------------------------------------------------------------

ADB_DIR = "adb"
IS_WINDOWS = platform.system() == "Windows"
ADB_BIN = "adb.exe" if IS_WINDOWS else "adb"
ADB_EXE = os.path.join(ADB_DIR, "platform-tools", ADB_BIN)

ADB_URLS = {
    "Windows": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
    "Darwin": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
    "Linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
}


def adb_path() -> str:
    if os.path.exists(ADB_EXE):
        return os.path.abspath(ADB_EXE)
    found = shutil.which("adb")
    return found or "adb"


def run_adb(args: str, serial: Optional[str] = None, timeout: int = 30) -> str:
    """Run an ADB command and return combined stdout/stderr text."""
    exe = adb_path()
    cmd = [exe]
    if serial:
        cmd += ["-s", serial]
    # split args respecting quotes; rely on shell=False for safety
    import shlex

    try:
        cmd += shlex.split(args, posix=not IS_WINDOWS)
    except ValueError:
        cmd += args.split()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        out = result.stdout.decode("utf-8", errors="replace")
        err = result.stderr.decode("utf-8", errors="replace")
        return (out + err).strip()
    except FileNotFoundError:
        return "ADB не найден"
    except subprocess.TimeoutExpired:
        return "⏱ Команда выполнялась слишком долго"


def adb_exists() -> bool:
    if os.path.exists(ADB_EXE):
        return True
    return shutil.which("adb") is not None


# ----------------------------------------------------------------------------
# Async worker
# ----------------------------------------------------------------------------


class AdbWorker(QObject):
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[[], str]):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.finished.emit(self._fn() or "")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


def run_async(parent: QObject, fn: Callable[[], str], on_done: Callable[[str], None]):
    thread = QThread(parent)
    worker = AdbWorker(fn)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(on_done)
    worker.finished.connect(thread.quit)
    worker.failed.connect(lambda e: on_done(f"❌ {e}"))
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread


# ----------------------------------------------------------------------------
# Live screen
# ----------------------------------------------------------------------------


class ScreenStreamer(QThread):
    frame = pyqtSignal(bytes)
    error = pyqtSignal(str)

    def __init__(self, serial: Optional[str], interval_ms: int = 700):
        super().__init__()
        self.serial = serial
        self.interval_ms = interval_ms
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        exe = adb_path()
        while self._running:
            cmd = [exe]
            if self.serial:
                cmd += ["-s", self.serial]
            cmd += ["exec-out", "screencap", "-p"]
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=10)
                if result.returncode == 0 and result.stdout:
                    self.frame.emit(result.stdout)
                else:
                    self.error.emit(result.stderr.decode("utf-8", errors="replace"))
            except Exception as exc:  # noqa: BLE001
                self.error.emit(str(exc))
            self.msleep(self.interval_ms)


class LiveScreenDialog(QDialog):
    def __init__(self, serial: Optional[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("📸 Live Screen")
        self.serial = serial
        self.display_w = 360
        self.display_h = 720
        self.img_w = self.img_h = 0
        self.device_w = self.device_h = 0

        self._read_device_size()

        self.label = QLabel("Подключаюсь…")
        self.label.setFixedSize(self.display_w, self.display_h)
        self.label.setStyleSheet("background:#000;color:#888;border:1px solid #333;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.mousePressEvent = self._on_click  # type: ignore[assignment]

        controls = self._build_controls()

        layout = QVBoxLayout(self)
        layout.addWidget(self.label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(controls)

        self.streamer = ScreenStreamer(serial)
        self.streamer.frame.connect(self._on_frame)
        self.streamer.start()

    def _read_device_size(self):
        out = run_adb("shell wm size", self.serial)
        for token in out.replace("Override size:", "Physical size:").split():
            if "x" in token and token.replace("x", "").isdigit():
                w, h = token.split("x")
                self.device_w, self.device_h = int(w), int(h)
                break

    def _build_controls(self) -> QGridLayout:
        grid = QGridLayout()
        keys = [
            ("Power", 26),
            ("Home", 3),
            ("Back", 4),
            ("Recent", 187),
            ("Wake", 224),
            ("Sleep", 223),
            ("Vol +", 24),
            ("Vol −", 25),
        ]
        for i, (label, code) in enumerate(keys):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, c=code: run_adb(f"shell input keyevent {c}", self.serial))
            grid.addWidget(btn, i // 4, i % 4)

        text_btn = QPushButton("✏ Ввести текст")
        text_btn.clicked.connect(self._send_text)
        grid.addWidget(text_btn, 2, 0, 1, 2)

        screenshot_btn = QPushButton("💾 Сохранить кадр")
        screenshot_btn.clicked.connect(self._save_screenshot)
        grid.addWidget(screenshot_btn, 2, 2, 1, 2)
        return grid

    def _on_click(self, event):
        if not self.img_w or not self.img_h:
            return
        x_local = event.position().x()
        y_local = event.position().y()
        if self.device_w and self.device_h:
            x = int(x_local * self.device_w / self.display_w)
            y = int(y_local * self.device_h / self.display_h)
        else:
            x = int(x_local * self.img_w / self.display_w)
            y = int(y_local * self.img_h / self.display_h)
        run_adb(f"shell input tap {x} {y}", self.serial)

    def _on_frame(self, data: bytes):
        img = QImage.fromData(data, "PNG")
        if img.isNull():
            return
        self.img_w, self.img_h = img.width(), img.height()
        pix = QPixmap.fromImage(img).scaled(
            self.display_w,
            self.display_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.label.setPixmap(pix)

    def _send_text(self):
        text, ok = QInputDialog.getText(self, "Ввод текста", "Текст для отправки:")
        if ok and text:
            safe = text.replace(" ", "%s").replace("'", "")
            run_adb(f'shell input text "{safe}"', self.serial)

    def _save_screenshot(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить скриншот", "screenshot.png", "PNG (*.png)"
        )
        if not path:
            return
        if self.label.pixmap() and not self.label.pixmap().isNull():
            self.label.pixmap().save(path, "PNG")

    def closeEvent(self, event):
        self.streamer.stop()
        self.streamer.wait(2000)
        super().closeEvent(event)


# ----------------------------------------------------------------------------
# Logcat
# ----------------------------------------------------------------------------


class LogcatStreamer(QThread):
    line = pyqtSignal(str)

    def __init__(self, serial: Optional[str], filter_text: str = ""):
        super().__init__()
        self.serial = serial
        self.filter_text = filter_text
        self._proc: Optional[subprocess.Popen] = None
        self._running = True

    def stop(self):
        self._running = False
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    def run(self):
        cmd = [adb_path()]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += ["logcat", "-v", "brief"]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
        except FileNotFoundError:
            self.line.emit("❌ ADB не найден")
            return

        assert self._proc.stdout is not None
        for raw in self._proc.stdout:
            if not self._running:
                break
            try:
                text = raw.decode("utf-8", errors="replace").rstrip()
            except Exception:  # noqa: BLE001
                continue
            if self.filter_text and self.filter_text.lower() not in text.lower():
                continue
            self.line.emit(text)


# ----------------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------------


DARK_QSS = """
QWidget {
    background: #1e1f22;
    color: #e6e6e6;
    font-family: 'Segoe UI', 'Helvetica Neue', sans-serif;
    font-size: 10pt;
}
QMainWindow, QDialog { background: #1e1f22; }
QPushButton {
    background: #2d2f34;
    border: 1px solid #3a3d44;
    border-radius: 6px;
    padding: 6px 12px;
}
QPushButton:hover { background: #3a3d44; border-color: #4f93ff; }
QPushButton:pressed { background: #4f93ff; color: white; }
QPushButton:disabled { color: #777; border-color: #333; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {
    background: #25272b;
    border: 1px solid #3a3d44;
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: #4f93ff;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: #4f93ff; }
QTabWidget::pane { border: 1px solid #3a3d44; border-radius: 4px; top: -1px; }
QTabBar::tab {
    background: #25272b;
    padding: 8px 16px;
    border: 1px solid #3a3d44;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected { background: #2d2f34; color: #4f93ff; }
QTabBar::tab:hover:!selected { background: #2a2c30; }
QGroupBox {
    border: 1px solid #3a3d44;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 6px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #4f93ff;
}
QStatusBar { background: #181a1d; border-top: 1px solid #3a3d44; }
QListWidget {
    background: #25272b;
    border: 1px solid #3a3d44;
    border-radius: 4px;
}
QListWidget::item:selected { background: #4f93ff; color: white; }
QProgressBar {
    background: #25272b;
    border: 1px solid #3a3d44;
    border-radius: 4px;
    text-align: center;
}
QProgressBar::chunk { background: #4f93ff; border-radius: 3px; }
QToolBar { background: #181a1d; border: none; spacing: 4px; padding: 4px; }
QScrollBar:vertical { background: #1e1f22; width: 10px; }
QScrollBar::handle:vertical { background: #3a3d44; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background: #4f93ff; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


@dataclass
class Device:
    serial: str
    state: str
    model: str = ""

    def label(self) -> str:
        suffix = f" — {self.model}" if self.model else ""
        return f"{self.serial} [{self.state}]{suffix}"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ADB Studio — PyQt6 Edition")
        self.resize(960, 680)
        self.current_serial: Optional[str] = None
        self.devices: list[Device] = []
        self.logcat_stream: Optional[LogcatStreamer] = None
        self._threads: list[QThread] = []

        self._build_ui()
        self._ensure_adb()
        QTimer.singleShot(300, self.refresh_devices)

        # auto-refresh device list every 5s
        self.device_timer = QTimer(self)
        self.device_timer.timeout.connect(self.refresh_devices)
        self.device_timer.start(5000)

    # ---- UI construction --------------------------------------------------

    def _build_ui(self):
        toolbar = QToolBar()
        toolbar.setIconSize(QSize(16, 16))
        self.addToolBar(toolbar)

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(280)
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)

        toolbar.addWidget(QLabel("  Устройство: "))
        toolbar.addWidget(self.device_combo)
        refresh = QAction("🔄 Обновить", self)
        refresh.triggered.connect(self.refresh_devices)
        toolbar.addAction(refresh)

        wireless = QAction("📶 Wi-Fi ADB", self)
        wireless.triggered.connect(self._wireless_connect)
        toolbar.addAction(wireless)

        kill = QAction("⏹ adb kill-server", self)
        kill.triggered.connect(lambda: self._async("kill-server", lambda: run_adb("kill-server")))
        toolbar.addAction(kill)

        tabs = QTabWidget()
        tabs.addTab(self._tab_overview(), "🏠 Главная")
        tabs.addTab(self._tab_apps(), "📦 Приложения")
        tabs.addTab(self._tab_files(), "📁 Файлы")
        tabs.addTab(self._tab_control(), "🎮 Управление")
        tabs.addTab(self._tab_logcat(), "📜 Logcat")
        tabs.addTab(self._tab_shell(), "💻 Shell")
        tabs.addTab(self._tab_root(), "🔓 Root / Bootloader")
        tabs.addTab(self._tab_settings(), "⚙ Настройки")
        self.setCentralWidget(tabs)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Готов")

    # ---- Tab: overview ----------------------------------------------------

    def _tab_overview(self) -> QWidget:
        page = QWidget()
        outer = QHBoxLayout(page)

        actions = QGroupBox("Быстрые действия")
        a = QGridLayout(actions)
        buttons = [
            ("📱 Проверить устройства", self.refresh_devices),
            ("📦 Установить APK", self.install_apk),
            ("🔁 Reboot", lambda: self._reboot("")),
            ("⚠ Recovery", lambda: self._reboot("recovery")),
            ("⚡ Bootloader", lambda: self._reboot("bootloader")),
            ("🪫 Battery info", self.show_battery),
            ("📸 Скриншот → ПК", self.save_screenshot),
            ("🎬 Запись экрана", self.record_screen),
            ("📋 Скопировать serial", self.copy_serial),
            ("🌐 dev-options docs", lambda: webbrowser.open("https://developer.android.com/studio/debug/dev-options")),
        ]
        for i, (label, fn) in enumerate(buttons):
            btn = QPushButton(label)
            btn.clicked.connect(fn)
            a.addWidget(btn, i // 2, i % 2)
        outer.addWidget(actions, 1)

        info = QGroupBox("Информация об устройстве")
        layout = QVBoxLayout(info)
        self.info_view = QTextEdit()
        self.info_view.setReadOnly(True)
        self.info_view.setFont(QFont("Consolas", 9))
        layout.addWidget(self.info_view)
        refresh_info = QPushButton("Обновить информацию")
        refresh_info.clicked.connect(self.refresh_device_info)
        layout.addWidget(refresh_info)
        outer.addWidget(info, 2)
        return page

    def refresh_device_info(self):
        if not self._require_device():
            return
        serial = self.current_serial

        def collect() -> str:
            props = [
                ("Модель", "ro.product.model"),
                ("Производитель", "ro.product.manufacturer"),
                ("Бренд", "ro.product.brand"),
                ("Android", "ro.build.version.release"),
                ("SDK", "ro.build.version.sdk"),
                ("Patch", "ro.build.version.security_patch"),
                ("Build", "ro.build.display.id"),
                ("ABI", "ro.product.cpu.abi"),
                ("Bootloader locked", "ro.boot.flash.locked"),
                ("KG state", "ro.boot.kg.state"),
                ("Serial", "ro.serialno"),
            ]
            lines = []
            for label, prop in props:
                value = run_adb(f"shell getprop {prop}", serial).strip() or "—"
                lines.append(f"{label:<22} {value}")
            size = run_adb("shell wm size", serial).strip()
            density = run_adb("shell wm density", serial).strip()
            uptime = run_adb("shell uptime", serial).strip()
            lines.append("")
            lines.append(size)
            lines.append(density)
            lines.append(uptime)
            return "\n".join(lines)

        self.info_view.setPlainText("Загружаю…")
        self._async("info", collect, lambda r: self.info_view.setPlainText(r))

    # ---- Tab: apps --------------------------------------------------------

    def _tab_apps(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        controls = QHBoxLayout()

        self.app_filter = QLineEdit()
        self.app_filter.setPlaceholderText("Поиск пакета…")
        self.app_filter.textChanged.connect(self._filter_packages)
        controls.addWidget(self.app_filter, 2)

        self.app_kind = QComboBox()
        self.app_kind.addItems(["Все", "Сторонние (-3)", "Системные (-s)", "Отключённые (-d)"])
        self.app_kind.currentIndexChanged.connect(self.refresh_packages)
        controls.addWidget(self.app_kind, 1)

        reload_btn = QPushButton("Обновить")
        reload_btn.clicked.connect(self.refresh_packages)
        controls.addWidget(reload_btn)
        v.addLayout(controls)

        self.app_list = QListWidget()
        self.app_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        v.addWidget(self.app_list, 1)

        actions = QHBoxLayout()
        for label, fn in [
            ("Деинсталлировать", self.uninstall_selected),
            ("Очистить данные", self.clear_data_selected),
            ("Force-stop", self.force_stop_selected),
            ("Запустить", self.launch_selected),
            ("Pull APK", self.pull_apk_selected),
        ]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            actions.addWidget(b)
        v.addLayout(actions)
        self._all_packages: list[str] = []
        return page

    def refresh_packages(self):
        if not self._require_device():
            return
        flag_map = {"Все": "", "Сторонние (-3)": "-3", "Системные (-s)": "-s", "Отключённые (-d)": "-d"}
        flag = flag_map[self.app_kind.currentText()]
        serial = self.current_serial

        def collect() -> str:
            return run_adb(f"shell pm list packages {flag}".strip(), serial)

        def done(out: str):
            self._all_packages = sorted(
                line.replace("package:", "").strip()
                for line in out.splitlines()
                if line.startswith("package:")
            )
            self._filter_packages()

        self.app_list.clear()
        self.app_list.addItem("Загружаю…")
        self._async("packages", collect, done)

    def _filter_packages(self):
        needle = self.app_filter.text().lower()
        self.app_list.clear()
        for pkg in self._all_packages:
            if needle in pkg.lower():
                self.app_list.addItem(QListWidgetItem(pkg))

    def _selected_packages(self) -> list[str]:
        return [item.text() for item in self.app_list.selectedItems()]

    def uninstall_selected(self):
        pkgs = self._selected_packages()
        if not pkgs or not self._require_device():
            return
        if QMessageBox.question(self, "Деинсталляция", f"Удалить {len(pkgs)} пакет(ов)?") != QMessageBox.StandardButton.Yes:
            return
        serial = self.current_serial

        def work() -> str:
            return "\n".join(f"{p}: {run_adb(f'uninstall {p}', serial)}" for p in pkgs)

        self._async("uninstall", work, self._show_log)

    def clear_data_selected(self):
        pkgs = self._selected_packages()
        if not pkgs or not self._require_device():
            return
        serial = self.current_serial
        self._async(
            "clear",
            lambda: "\n".join(f"{p}: {run_adb(f'shell pm clear {p}', serial)}" for p in pkgs),
            self._show_log,
        )

    def force_stop_selected(self):
        pkgs = self._selected_packages()
        if not pkgs or not self._require_device():
            return
        serial = self.current_serial
        self._async(
            "stop",
            lambda: "\n".join(f"{p}: {run_adb(f'shell am force-stop {p}', serial) or 'OK'}" for p in pkgs),
            self._show_log,
        )

    def launch_selected(self):
        pkgs = self._selected_packages()
        if not pkgs or not self._require_device():
            return
        serial = self.current_serial
        self._async(
            "launch",
            lambda: "\n".join(
                f"{p}: {run_adb(f'shell monkey -p {p} -c android.intent.category.LAUNCHER 1', serial)}"
                for p in pkgs
            ),
            self._show_log,
        )

    def pull_apk_selected(self):
        pkgs = self._selected_packages()
        if not pkgs or not self._require_device():
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Куда сохранить APK?")
        if not out_dir:
            return
        serial = self.current_serial

        def work() -> str:
            results = []
            for pkg in pkgs:
                path_out = run_adb(f"shell pm path {pkg}", serial).strip()
                first = next((ln.replace("package:", "") for ln in path_out.splitlines() if ln.startswith("package:")), "")
                if not first:
                    results.append(f"{pkg}: путь не найден")
                    continue
                target = os.path.join(out_dir, f"{pkg}.apk")
                results.append(f"{pkg} -> {target}\n{run_adb(f'pull {first} {target}', serial)}")
            return "\n".join(results)

        self._async("pull-apk", work, self._show_log)

    # ---- Tab: files -------------------------------------------------------

    def _tab_files(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        push = QGroupBox("Push (ПК → телефон)")
        pl = QGridLayout(push)
        self.push_local = QLineEdit()
        local_btn = QPushButton("Файл…")
        local_btn.clicked.connect(self._pick_push_file)
        self.push_remote = QLineEdit("/sdcard/Download/")
        push_btn = QPushButton("⬆ Push")
        push_btn.clicked.connect(self._do_push)
        pl.addWidget(QLabel("Локальный:"), 0, 0)
        pl.addWidget(self.push_local, 0, 1)
        pl.addWidget(local_btn, 0, 2)
        pl.addWidget(QLabel("Удалённый:"), 1, 0)
        pl.addWidget(self.push_remote, 1, 1)
        pl.addWidget(push_btn, 1, 2)
        layout.addWidget(push)

        pull = QGroupBox("Pull (телефон → ПК)")
        pll = QGridLayout(pull)
        self.pull_remote = QLineEdit("/sdcard/")
        self.pull_local = QLineEdit(os.path.expanduser("~"))
        local_btn2 = QPushButton("Папка…")
        local_btn2.clicked.connect(self._pick_pull_dir)
        pull_btn = QPushButton("⬇ Pull")
        pull_btn.clicked.connect(self._do_pull)
        pll.addWidget(QLabel("Удалённый:"), 0, 0)
        pll.addWidget(self.pull_remote, 0, 1)
        pll.addWidget(QLabel("Локальный:"), 1, 0)
        pll.addWidget(self.pull_local, 1, 1)
        pll.addWidget(local_btn2, 1, 2)
        pll.addWidget(pull_btn, 2, 1)
        layout.addWidget(pull)

        ls = QGroupBox("Просмотр (ls)")
        lsl = QHBoxLayout(ls)
        self.ls_path = QLineEdit("/sdcard/")
        self.ls_path.returnPressed.connect(self._do_ls)
        lsl.addWidget(self.ls_path, 1)
        ls_btn = QPushButton("ls -lah")
        ls_btn.clicked.connect(self._do_ls)
        lsl.addWidget(ls_btn)
        layout.addWidget(ls)

        self.files_log = QPlainTextEdit()
        self.files_log.setReadOnly(True)
        self.files_log.setFont(QFont("Consolas", 9))
        layout.addWidget(self.files_log, 1)
        return page

    def _pick_push_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выбрать файл")
        if path:
            self.push_local.setText(path)

    def _pick_pull_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Выбрать папку")
        if path:
            self.pull_local.setText(path)

    def _do_push(self):
        if not self._require_device():
            return
        local = self.push_local.text().strip()
        remote = self.push_remote.text().strip()
        if not local or not remote:
            return
        serial = self.current_serial
        self._async(
            "push",
            lambda: run_adb(f'push "{local}" "{remote}"', serial, timeout=600),
            lambda r: self.files_log.appendPlainText(r),
        )

    def _do_pull(self):
        if not self._require_device():
            return
        remote = self.pull_remote.text().strip()
        local = self.pull_local.text().strip()
        if not remote or not local:
            return
        serial = self.current_serial
        self._async(
            "pull",
            lambda: run_adb(f'pull "{remote}" "{local}"', serial, timeout=600),
            lambda r: self.files_log.appendPlainText(r),
        )

    def _do_ls(self):
        if not self._require_device():
            return
        path = self.ls_path.text().strip() or "/sdcard/"
        serial = self.current_serial
        self._async(
            "ls",
            lambda: run_adb(f"shell ls -lah '{path}'", serial),
            lambda r: self.files_log.appendPlainText(f"\n$ ls {path}\n{r}"),
        )

    # ---- Tab: remote control ---------------------------------------------

    def _tab_control(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Открой Live экран для просмотра и управления телефоном."))
        b = QPushButton("📸 Открыть Live Screen")
        b.clicked.connect(self.open_live_screen)
        layout.addWidget(b)

        keys = QGroupBox("Быстрые клавиши (без зеркалирования)")
        g = QGridLayout(keys)
        items = [
            ("Power", 26), ("Home", 3), ("Back", 4), ("Recent", 187),
            ("Wake", 224), ("Sleep", 223), ("Vol +", 24), ("Vol −", 25),
            ("Camera", 27), ("Search", 84), ("Menu", 82), ("Mute", 164),
        ]
        for i, (label, code) in enumerate(items):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, c=code: self._async(
                "key", lambda c=c: run_adb(f"shell input keyevent {c}", self.current_serial), lambda r: None
            ))
            g.addWidget(btn, i // 4, i % 4)
        layout.addWidget(keys)

        layout.addStretch(1)
        return page

    def open_live_screen(self):
        if not self._require_device():
            return
        dlg = LiveScreenDialog(self.current_serial, self)
        dlg.exec()

    # ---- Tab: logcat ------------------------------------------------------

    def _tab_logcat(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.logcat_filter = QLineEdit()
        self.logcat_filter.setPlaceholderText("Подстрока для фильтрации (regex не поддерживается)")
        bar.addWidget(self.logcat_filter, 2)
        self.logcat_start = QPushButton("▶ Старт")
        self.logcat_start.clicked.connect(self._start_logcat)
        self.logcat_stop = QPushButton("⏹ Стоп")
        self.logcat_stop.clicked.connect(self._stop_logcat)
        self.logcat_stop.setEnabled(False)
        clear = QPushButton("Очистить вывод")
        clear.clicked.connect(lambda: self.logcat_view.clear())
        wipe = QPushButton("logcat -c")
        wipe.clicked.connect(lambda: self._async("logcat-c", lambda: run_adb("logcat -c", self.current_serial), lambda r: None))
        bar.addWidget(self.logcat_start)
        bar.addWidget(self.logcat_stop)
        bar.addWidget(clear)
        bar.addWidget(wipe)
        layout.addLayout(bar)

        self.logcat_view = QPlainTextEdit()
        self.logcat_view.setReadOnly(True)
        self.logcat_view.setFont(QFont("Consolas", 9))
        self.logcat_view.setMaximumBlockCount(5000)
        layout.addWidget(self.logcat_view, 1)
        return page

    def _start_logcat(self):
        if not self._require_device():
            return
        self._stop_logcat()
        self.logcat_view.clear()
        self.logcat_stream = LogcatStreamer(self.current_serial, self.logcat_filter.text().strip())
        self.logcat_stream.line.connect(self.logcat_view.appendPlainText)
        self.logcat_stream.start()
        self.logcat_start.setEnabled(False)
        self.logcat_stop.setEnabled(True)

    def _stop_logcat(self):
        if self.logcat_stream:
            self.logcat_stream.stop()
            self.logcat_stream.wait(1500)
            self.logcat_stream = None
        self.logcat_start.setEnabled(True)
        self.logcat_stop.setEnabled(False)

    # ---- Tab: shell -------------------------------------------------------

    def _tab_shell(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.shell_input = QLineEdit()
        self.shell_input.setPlaceholderText("команда (без префикса 'adb')   например: shell dumpsys battery")
        self.shell_input.returnPressed.connect(self._run_shell)
        bar.addWidget(self.shell_input, 1)
        run_btn = QPushButton("Run")
        run_btn.clicked.connect(self._run_shell)
        bar.addWidget(run_btn)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.shell_view.clear())
        bar.addWidget(clear)
        layout.addLayout(bar)

        self.shell_view = QPlainTextEdit()
        self.shell_view.setReadOnly(True)
        self.shell_view.setFont(QFont("Consolas", 9))
        layout.addWidget(self.shell_view, 1)
        return page

    def _run_shell(self):
        if not self._require_device():
            return
        cmd = self.shell_input.text().strip()
        if not cmd:
            return
        serial = self.current_serial
        self.shell_view.appendPlainText(f"\n$ adb {cmd}")
        self._async("shell", lambda: run_adb(cmd, serial, timeout=120),
                    lambda r: self.shell_view.appendPlainText(r))

    # ---- Tab: root --------------------------------------------------------

    def _tab_root(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        actions = [
            ("🔐 Проверить root (su)", self._check_root),
            ("⚡ adb root", self._request_adb_root),
            ("🧠 Подсказка по бренду", self._brand_help),
            ("📋 OEM Unlock checker (Samsung)", self._oem_check),
            ("🔓 Bootloader status (fastboot)", self._bootloader_status),
            ("🛠 Магиск установлен?", self._check_magisk),
            ("⬇ Reboot to Download (Samsung)", lambda: self._reboot("download")),
            ("⬇ Reboot to Bootloader / Fastboot", lambda: self._reboot("bootloader")),
        ]
        for label, fn in actions:
            b = QPushButton(label)
            b.clicked.connect(fn)
            layout.addWidget(b)
        layout.addStretch(1)

        self.root_log = QTextEdit()
        self.root_log.setReadOnly(True)
        self.root_log.setFont(QFont("Consolas", 9))
        layout.addWidget(self.root_log, 1)
        return page

    def _root_log(self, text: str):
        self.root_log.append(text + "\n")
        self.root_log.moveCursor(QTextCursor.MoveOperation.End)

    def _check_root(self):
        if not self._require_device():
            return
        serial = self.current_serial
        self._async(
            "root",
            lambda: run_adb("shell su -c id", serial),
            lambda out: self._root_log("✅ Root доступен" if "uid=0" in out else f"❌ Root не получен:\n{out}"),
        )

    def _request_adb_root(self):
        if not self._require_device():
            return
        serial = self.current_serial
        self._async("adbroot", lambda: run_adb("root", serial), self._root_log)

    def _check_magisk(self):
        if not self._require_device():
            return
        serial = self.current_serial
        self._async(
            "magisk",
            lambda: run_adb("shell pm list packages", serial),
            lambda out: self._root_log(
                "✅ Magisk установлен" if "com.topjohnwu.magisk" in out else "❌ Magisk не найден"
            ),
        )

    def _bootloader_status(self):
        def work() -> str:
            try:
                return subprocess.check_output(
                    ["fastboot", "oem", "device-info"],
                    stderr=subprocess.STDOUT,
                    timeout=10,
                ).decode("utf-8", errors="replace")
            except FileNotFoundError:
                return "❌ fastboot не найден в PATH"
            except subprocess.CalledProcessError as e:
                return e.output.decode("utf-8", errors="replace")
            except Exception as e:  # noqa: BLE001
                return f"❌ {e}"

        def done(text: str):
            if "unlocked: yes" in text.lower() or "Device unlocked: true" in text:
                self._root_log(f"🔓 Bootloader разблокирован\n{text}")
            else:
                self._root_log(f"🔒 Bootloader заблокирован / нет fastboot устройства\n{text}")

        self._async("fastboot", work, done)

    def _brand_help(self):
        if not self._require_device():
            return
        serial = self.current_serial

        def work() -> str:
            return run_adb("shell getprop ro.product.manufacturer", serial).strip().lower()

        def done(brand: str):
            tips = {
                "xiaomi": "Xiaomi: USB-debug → OEM Unlock → Mi Unlock → fastboot flashing unlock → Magisk",
                "redmi": "Redmi: то же, что Xiaomi (Mi Unlock + Magisk)",
                "samsung": "Samsung: USB-debug → OEM Unlock → Download Mode → Odin (AP+Magisk)",
                "huawei": "Huawei: 99% моделей не разблокируются официально",
                "google": "Pixel: OEM Unlock → fastboot flashing unlock → patched boot.img + Magisk",
                "oneplus": "OnePlus: fastboot oem unlock → patched boot.img + Magisk",
            }
            for key, txt in tips.items():
                if key in brand:
                    self._root_log(f"📱 {brand}\n{txt}")
                    return
            self._root_log(f"📱 {brand or 'неизвестно'}\nНет специфичной инструкции")

        self._async("brand", work, done)

    def _oem_check(self):
        if not self._require_device():
            return
        serial = self.current_serial

        def work() -> str:
            man = run_adb("shell getprop ro.product.manufacturer", serial).lower()
            if "samsung" not in man:
                return "Не Samsung — OEM Unlock не требуется."
            model = run_adb("shell getprop ro.product.model", serial).strip()
            kg = run_adb("shell getprop ro.boot.kg.state", serial).strip().lower()
            flash = run_adb("shell getprop ro.boot.flash.locked", serial).strip()
            frp = run_adb("shell getprop ro.boot.frp.pst", serial).strip()
            lines = [f"📱 Модель: {model}"]
            if model.endswith("U") or model.endswith("U1"):
                lines.append("🚫 Carrier-locked (USA) — bootloader залочен навсегда.")
                return "\n".join(lines)
            if kg == "prenormal":
                lines.append("⏳ KG: prenormal — нужен полный 7-дневный прогрев + Samsung+Google аккаунты.")
                return "\n".join(lines)
            if frp:
                lines.append("🔒 FRP активен — выйди из Google аккаунта.")
                return "\n".join(lines)
            if flash == "1":
                lines.append("✅ OEM Unlock должен быть доступен в developer settings.")
            else:
                lines.append("🔓 Bootloader уже разблокирован (flash.locked=0).")
            return "\n".join(lines)

        self._async("oem", work, self._root_log)

    # ---- Tab: settings ----------------------------------------------------

    def _tab_settings(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        adb_box = QGroupBox("ADB")
        al = QVBoxLayout(adb_box)
        self.adb_status = QLabel()
        al.addWidget(self.adb_status)
        install = QPushButton("⬇ Установить platform-tools локально")
        install.clicked.connect(self._install_adb)
        al.addWidget(install)
        version = QPushButton("adb version")
        version.clicked.connect(lambda: self._async("ver", lambda: run_adb("version"),
                                                    lambda r: QMessageBox.information(self, "adb version", r)))
        al.addWidget(version)
        layout.addWidget(adb_box)

        wifi_box = QGroupBox("Беспроводной ADB (TCP/IP)")
        wl = QFormLayout(wifi_box)
        self.wifi_host = QLineEdit("192.168.0.100")
        self.wifi_port = QSpinBox()
        self.wifi_port.setRange(1, 65535)
        self.wifi_port.setValue(5555)
        connect = QPushButton("Подключиться")
        connect.clicked.connect(self._wireless_connect_form)
        tcpip = QPushButton("Перевести подключённое устройство в tcpip:5555")
        tcpip.clicked.connect(lambda: self._async(
            "tcpip",
            lambda: run_adb(f"tcpip {self.wifi_port.value()}", self.current_serial),
            self._show_log,
        ))
        wl.addRow("Хост:", self.wifi_host)
        wl.addRow("Порт:", self.wifi_port)
        wl.addRow(connect)
        wl.addRow(tcpip)
        layout.addWidget(wifi_box)

        about = QGroupBox("О программе")
        about_l = QVBoxLayout(about)
        about_l.addWidget(QLabel("ADB Studio — миграция оригинального tkinter GUI на PyQt6.\n"
                                  "Поддерживает множественные устройства, Wi-Fi ADB, logcat,\n"
                                  "управление пакетами, файловые операции, Live Screen и многое другое."))
        layout.addWidget(about)
        layout.addStretch(1)
        self._refresh_adb_status()
        return page

    def _refresh_adb_status(self):
        if adb_exists():
            self.adb_status.setText(f"✅ ADB найден: {adb_path()}")
        else:
            self.adb_status.setText("❌ ADB не найден — установи platform-tools")

    def _install_adb(self):
        system = platform.system()
        url = ADB_URLS.get(system)
        if not url:
            QMessageBox.warning(self, "ADB", f"Платформа {system} не поддерживается автоустановкой.")
            return
        self.status.showMessage(f"Скачиваю platform-tools для {system}…")

        def work() -> str:
            try:
                os.makedirs(ADB_DIR, exist_ok=True)
                zip_path = os.path.join(ADB_DIR, "adb.zip")
                urllib.request.urlretrieve(url, zip_path)
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(ADB_DIR)
                os.remove(zip_path)
                # ensure executable bit on Unix
                if not IS_WINDOWS:
                    bin_path = os.path.join(ADB_DIR, "platform-tools", "adb")
                    if os.path.exists(bin_path):
                        os.chmod(bin_path, 0o755)
                return "✅ platform-tools установлены"
            except Exception as e:  # noqa: BLE001
                return f"❌ {e}"

        self._async("install-adb", work, lambda r: (self._show_log(r), self._refresh_adb_status()))

    def _wireless_connect(self):
        host, ok = QInputDialog.getText(self, "Wi-Fi ADB", "host:port (например 192.168.0.100:5555):")
        if ok and host:
            self._do_wireless(host)

    def _wireless_connect_form(self):
        self._do_wireless(f"{self.wifi_host.text().strip()}:{self.wifi_port.value()}")

    def _do_wireless(self, hostport: str):
        self._async(
            "wifi",
            lambda: run_adb(f"connect {hostport}"),
            lambda r: (self._show_log(r), self.refresh_devices()),
        )

    # ---- Device discovery -------------------------------------------------

    def refresh_devices(self):
        def work() -> str:
            return run_adb("devices -l")

        def done(out: str):
            devices: list[Device] = []
            for line in out.splitlines()[1:]:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                serial, state = parts[0], parts[1]
                model = ""
                for token in parts[2:]:
                    if token.startswith("model:"):
                        model = token.split(":", 1)[1]
                devices.append(Device(serial=serial, state=state, model=model))
            self._update_devices(devices)

        self._async("devices", work, done)

    def _update_devices(self, devices: list[Device]):
        self.devices = devices
        prev = self.current_serial
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        if not devices:
            self.device_combo.addItem("— устройства не найдены —", None)
        for d in devices:
            self.device_combo.addItem(d.label(), d.serial)
        # restore selection
        if prev:
            idx = self.device_combo.findData(prev)
            if idx >= 0:
                self.device_combo.setCurrentIndex(idx)
        self.device_combo.blockSignals(False)
        self._on_device_changed()

    def _on_device_changed(self):
        self.current_serial = self.device_combo.currentData()
        if self.current_serial:
            self.status.showMessage(f"Активное устройство: {self.current_serial}")
        else:
            self.status.showMessage("Устройство не выбрано")

    def _require_device(self) -> bool:
        if self.current_serial:
            return True
        QMessageBox.information(
            self,
            "Устройство не подключено",
            "1. Подключи телефон по USB\n"
            "2. Включи USB-отладку (Настройки → О телефоне → 7 раз по «Номер сборки» → Для разработчиков)\n"
            "3. Подтверди разрешение на телефоне\n"
            "4. Нажми «🔄 Обновить» вверху",
        )
        return False

    # ---- Misc actions -----------------------------------------------------

    def install_apk(self):
        if not self._require_device():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Выбери APK", filter="APK (*.apk *.apks *.xapk)")
        if not path:
            return
        serial = self.current_serial
        self._async("install", lambda: run_adb(f'install -r "{path}"', serial, timeout=600), self._show_log)

    def show_battery(self):
        if not self._require_device():
            return
        serial = self.current_serial
        self._async("battery", lambda: run_adb("shell dumpsys battery", serial), self._show_log)

    def save_screenshot(self):
        if not self._require_device():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить скриншот", "screenshot.png", "PNG (*.png)")
        if not path:
            return
        serial = self.current_serial

        def work() -> str:
            cmd = [adb_path()]
            if serial:
                cmd += ["-s", serial]
            cmd += ["exec-out", "screencap", "-p"]
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=30)
                if result.returncode != 0 or not result.stdout:
                    return result.stderr.decode("utf-8", errors="replace") or "❌ пустой вывод"
                with open(path, "wb") as fh:
                    fh.write(result.stdout)
                return f"✅ Сохранено: {path}"
            except Exception as e:  # noqa: BLE001
                return f"❌ {e}"

        self._async("screenshot", work, self._show_log)

    def record_screen(self):
        if not self._require_device():
            return
        secs, ok = QInputDialog.getInt(self, "Запись экрана", "Длительность (сек, max 180):", 30, 1, 180)
        if not ok:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить видео", "screen.mp4", "MP4 (*.mp4)")
        if not path:
            return
        serial = self.current_serial
        remote = "/sdcard/screen_record.mp4"

        def work() -> str:
            log = []
            log.append(run_adb(f"shell screenrecord --time-limit {secs} {remote}", serial, timeout=secs + 30))
            log.append(run_adb(f'pull {remote} "{path}"', serial, timeout=120))
            log.append(run_adb(f"shell rm {remote}", serial))
            return "\n".join(log)

        self.status.showMessage(f"Записываю {secs}s…")
        self._async("record", work, self._show_log)

    def copy_serial(self):
        if not self.current_serial:
            return
        QApplication.clipboard().setText(self.current_serial)
        self.status.showMessage(f"Скопировано: {self.current_serial}", 3000)

    def _reboot(self, mode: str):
        if not self._require_device():
            return
        serial = self.current_serial
        target = mode or "обычная перезагрузка"
        if QMessageBox.question(self, "Reboot", f"Перезагрузить устройство ({target})?") != QMessageBox.StandardButton.Yes:
            return
        self._async("reboot", lambda: run_adb(f"reboot {mode}".strip(), serial), self._show_log)

    # ---- helpers ----------------------------------------------------------

    def _async(self, name: str, fn: Callable[[], str], on_done: Callable[[str], None]):
        self.status.showMessage(f"▶ {name}…")

        def wrapped(result: str):
            try:
                on_done(result)
            finally:
                self.status.showMessage(f"✓ {name}", 2000)

        thread = run_async(self, fn, wrapped)
        self._threads.append(thread)
        # cleanup finished threads from list
        thread.finished.connect(lambda: self._threads.remove(thread) if thread in self._threads else None)

    def _show_log(self, text: str):
        QMessageBox.information(self, "Результат", text or "(пусто)")

    def _ensure_adb(self):
        if adb_exists():
            return
        if QMessageBox.question(
            self,
            "ADB не найден",
            "ADB не установлен. Скачать platform-tools автоматически?",
        ) == QMessageBox.StandardButton.Yes:
            self._install_adb()

    def closeEvent(self, event):
        self._stop_logcat()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
