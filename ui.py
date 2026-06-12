import base64
import os
import re
import sys
from datetime import date

from components import ThemedOptionCardPlane
from icons import IconDictionary
from PyQt5.Qt import QColor, QPoint, QFont, QTextCharFormat, QIcon
from PyQt5.QtCore import Qt, pyqtSignal, QCoreApplication, QDate, QSize, QTimer
from PyQt5.QtWidgets import (
    QCalendarWidget,
    QComboBox,
    QDateEdit,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QApplication,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from settings_parser import SettingsParser
from todos_parser import TODOParser
from feishu_sync import (
    IMPORT_RANGE_DAYS,
    FeishuConfig,
    FeishuImportError,
    FeishuImportService,
)

from siui.components.widgets import (
    SiCheckBox,
    SiDenseHContainer,
    SiDenseVContainer,
    SiPixLabel,
    SiLabel,
    SiSimpleButton,
    SiSvgLabel,
    SiSwitch,
    SiToggleButton,
)
from siui.core.animation import SiExpAnimation
from siui.core.color import Color
from siui.core.globals import NewGlobal, SiGlobal
from siui.gui.tooltip import ToolTipWindow

# 创建删除队列
SiGlobal.todo_list = NewGlobal()
SiGlobal.todo_list.delete_pile = []

# 创建锁定位置变量
SiGlobal.todo_list.position_locked = False

# 创建设置文件解析器并写入全局变量
def get_runtime_data_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


RUNTIME_DATA_DIR = get_runtime_data_dir()
OPTIONS_INI_PATH = os.path.join(RUNTIME_DATA_DIR, "options.ini")
TODOS_INI_PATH = os.path.join(RUNTIME_DATA_DIR, "todos.ini")

SiGlobal.todo_list.settings_parser = SettingsParser(OPTIONS_INI_PATH)
SiGlobal.todo_list.todos_parser = TODOParser(TODOS_INI_PATH)

DEFAULT_SETTINGS = {
    "USE_DARK_MODE": False,
    "FIXED_POSITION": False,
    "FIXED_POSITION_X": 120,
    "FIXED_POSITION_Y": 120,
    "ADD_TODO_DAY_PLAN_ENABLED": True,
    "FEISHU_APP_ID": "",
    "FEISHU_APP_SECRET": "",
    "FEISHU_BITABLE_APP_TOKEN": "",
    "FEISHU_BITABLE_TABLE_ID": "",
    "FEISHU_BITABLE_VIEW_ID": "",
    "FEISHU_FIELD_TITLE": "",
    "FEISHU_FIELD_DATE": "",
    "FEISHU_FIELD_DAILY_REPORT": "",
    "FEISHU_REPORT_API_ENABLED": True,
    "FEISHU_BITABLE_ENABLED": True,
}
_settings_changed = False
for _setting_key, _setting_value in DEFAULT_SETTINGS.items():
    if _setting_key not in SiGlobal.todo_list.settings_parser.options:
        SiGlobal.todo_list.settings_parser.options[_setting_key] = _setting_value
        _settings_changed = True
if _settings_changed:
    SiGlobal.todo_list.settings_parser.write()

TASK_DATE_PATTERN = re.compile(r"^\[DATE=(\d{4}-\d{2}-\d{2})\]\s*(.*)$", re.S)


def parse_task_entry(entry: str):
    match = TASK_DATE_PATTERN.match(entry)
    if match:
        task_date = match.group(1)
        task_text = canonicalize_calendar_task_text(match.group(2))
        return task_text, task_date
    return entry, None


def build_task_entry(task_text: str, task_date: str = None):
    normalized_text = task_text.strip()
    if task_date:
        return f"[DATE={task_date}] {normalized_text}"
    return normalized_text


EVENT_TYPE_LABELS = {
    "normal": "\u666e\u901a",
    "important": "\u91cd\u8981",
    "anniversary": "\u7eaa\u5ff5\u65e5",
}
EVENT_LABEL_TO_TYPE = {label: key for key, label in EVENT_TYPE_LABELS.items()}
EVENT_LABEL_ALIASES = {
    "\u666e\u901a": "normal",
    "\u91cd\u8981": "important",
    "\u7eaa\u5ff5\u65e5": "anniversary",
    "鏅��": "normal",
    "閲嶈": "important",
    "绾康鏃�": "anniversary",
    "??": "normal",
    "?!": "important",
    "???": "anniversary",
}
EVENT_TYPE_TO_PRIMARY_LABEL = {
    "normal": "\u666e\u901a",
    "important": "\u91cd\u8981",
    "anniversary": "\u7eaa\u5ff5\u65e5",
}
CALENDAR_EVENT_PATTERN = re.compile(r"^\[(\u666e\u901a|\u91cd\u8981|\u7eaa\u5ff5\u65e5)\]\s*(.*?)(?:\s*[|｜]\s*(.*))?$", re.S)


def normalize_calendar_event_raw_text(raw_text: str):
    normalized = (raw_text or "").strip()
    if not normalized.startswith("["):
        return normalized

    normalized = normalized.replace("锝?", "｜").replace("锝�", "｜")
    normalized = normalized.replace(" | ", " ｜ ")

    for alias_label, event_type in EVENT_LABEL_ALIASES.items():
        primary_label = EVENT_TYPE_TO_PRIMARY_LABEL.get(event_type)
        if primary_label is None:
            continue
        normalized = normalized.replace(f"[{alias_label}]", f"[{primary_label}]")
    return normalized


def encode_calendar_event(title: str, event_type: str = "normal", description: str = ""):
    event_label = EVENT_TYPE_LABELS.get(event_type, EVENT_TYPE_LABELS["normal"])
    normalized_title = (title or "").strip()
    normalized_description = (description or "").strip()

    if normalized_description:
        return f"[{event_label}] {normalized_title} ｜ {normalized_description}"
    return f"[{event_label}] {normalized_title}"


def parse_calendar_event_text(task_text: str):
    raw_original = (task_text or "").strip()
    raw_text = normalize_calendar_event_raw_text(raw_original)
    match = CALENDAR_EVENT_PATTERN.match(raw_text)
    if not match:
        return {
            "structured": False,
            "type": "normal",
            "label": EVENT_TYPE_LABELS["normal"],
            "title": raw_text,
            "description": "",
            "raw_text": raw_original,
            "normalized_text": raw_text,
        }

    label = match.group(1)
    title = (match.group(2) or "").strip()
    description = (match.group(3) or "").strip()
    event_type = EVENT_LABEL_TO_TYPE.get(label, "normal")
    return {
        "structured": True,
        "type": event_type,
        "label": label,
        "title": title,
        "description": description,
        "raw_text": raw_original,
        "normalized_text": raw_text,
    }


def canonicalize_calendar_task_text(task_text: str):
    parsed = parse_calendar_event_text(task_text)
    if not parsed["structured"]:
        return parsed["normalized_text"]

    title = parsed["title"]
    description = parsed["description"]

    if re.fullmatch(r"\?+", title or ""):
        title = "\u672a\u547d\u540d\u8ba1\u5212"
    if re.fullmatch(r"\?+", description or ""):
        description = ""

    return encode_calendar_event(title=title, event_type=parsed["type"], description=description)


# Fallback calendar icon (used when external image cannot be loaded).
CALENDAR_BUTTON_ICON_SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">'
    '<defs>'
    '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#34B7EE"/>'
    '<stop offset="100%" stop-color="#1E9FDE"/>'
    '</linearGradient>'
    '</defs>'
    '<circle cx="12" cy="12" r="11.2" fill="url(#bg)"/>'
    '<rect x="4.5" y="6.8" width="15" height="12.7" rx="2.1" fill="#FFFFFF"/>'
    '<rect x="4.5" y="6.8" width="15" height="3.4" rx="2.1" fill="#FFFFFF"/>'
    '<rect x="6.4" y="10.8" width="11.2" height="6.7" rx="1.2" fill="#1E9FDE"/>'
    '<line x1="8.1" y1="5.4" x2="8.1" y2="8.1" stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round"/>'
    '<line x1="12" y1="5.4" x2="12" y2="8.1" stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round"/>'
    '<line x1="15.9" y1="5.4" x2="15.9" y2="8.1" stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round"/>'
    '</svg>'
).encode()


def resource_path(relative_path: str):
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


CALENDAR_ICON_IMAGE_PATH = resource_path(os.path.join("assets", "custom-icons", "calendar.png"))
CALENDAR_RIGHT_ARROW_PATH = resource_path(os.path.join("assets", "custom-icons", "right-arrow.png"))
CALENDAR_LEFT_ARROW_PATH = resource_path(os.path.join("assets", "custom-icons", "left-arrow.png"))


def get_calendar_button_icon_data():
    if not os.path.exists(CALENDAR_ICON_IMAGE_PATH):
        return CALENDAR_BUTTON_ICON_SVG

    try:
        with open(CALENDAR_ICON_IMAGE_PATH, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">'
            '<image href="data:image/png;base64,'
            + encoded +
            '" x="0" y="0" width="24" height="24" preserveAspectRatio="xMidYMid meet"/>'
            '</svg>'
        )
        return svg.encode("utf-8")
    except Exception:
        return CALENDAR_BUTTON_ICON_SVG

def lock_position(state):
    SiGlobal.todo_list.position_locked = state


# 主题颜色
def load_colors(is_dark=True):
    if is_dark is True:  # 深色主题
        # 加载图标
        SiGlobal.siui.icons.update(IconDictionary(color="#e1d9e8").icons)

        # 设置颜色
        SiGlobal.siui.colors["THEME"] = "#e1d9e8"
        SiGlobal.siui.colors["PANEL_THEME"] = "#0F85D3"
        SiGlobal.siui.colors["BACKGROUND_COLOR"] = "#252229"
        SiGlobal.siui.colors["BACKGROUND_DARK_COLOR"] = SiGlobal.siui.colors["INTERFACE_BG_A"]
        SiGlobal.siui.colors["BORDER_COLOR"] = "#3b373f"
        SiGlobal.siui.colors["TOOLTIP_BG"] = "ef413a47"
        SiGlobal.siui.colors["SVG_A"] = SiGlobal.siui.colors["THEME"]

        SiGlobal.siui.colors["THEME_TRANSITION_A"] = "#52389a"
        SiGlobal.siui.colors["THEME_TRANSITION_B"] = "#9c4e8b"

        SiGlobal.siui.colors["TEXT_A"] = "#FFFFFF"
        SiGlobal.siui.colors["TEXT_B"] = "#e1d9e8"
        SiGlobal.siui.colors["TEXT_C"] = Color.transparency(SiGlobal.siui.colors["THEME"], 0.75)
        SiGlobal.siui.colors["TEXT_D"] = Color.transparency(SiGlobal.siui.colors["THEME"], 0.6)
        SiGlobal.siui.colors["TEXT_E"] = Color.transparency(SiGlobal.siui.colors["THEME"], 0.5)

        SiGlobal.siui.colors["SWITCH_DEACTIVATE"] = "#D2D2D2"
        SiGlobal.siui.colors["SWITCH_ACTIVATE"] = "#100912"

        SiGlobal.siui.colors["BUTTON_HOVER"] = "#10FFFFFF"
        SiGlobal.siui.colors["BUTTON_FLASH"] = "#20FFFFFF"

        SiGlobal.siui.colors["SIMPLE_BUTTON_BG"] = Color.transparency(SiGlobal.siui.colors["THEME"], 0.1)

        SiGlobal.siui.colors["TOGGLE_BUTTON_OFF_BG"] = Color.transparency(SiGlobal.siui.colors["THEME"], 0)
        SiGlobal.siui.colors["TOGGLE_BUTTON_ON_BG"] = Color.transparency(SiGlobal.siui.colors["THEME"], 0.1)

    else:  # 亮色主题
        # 加载图标
        SiGlobal.siui.icons.update(IconDictionary(color="#0F85D3").icons)

        # 设置颜色
        SiGlobal.siui.colors["THEME"] = "#0F85D3"
        SiGlobal.siui.colors["PANEL_THEME"] = "#0F85D3"
        SiGlobal.siui.colors["BACKGROUND_COLOR"] = "#F3F3F3"
        SiGlobal.siui.colors["BACKGROUND_DARK_COLOR"] = "#e8e8e8"
        SiGlobal.siui.colors["BORDER_COLOR"] = "#d0d0d0"
        SiGlobal.siui.colors["TOOLTIP_BG"] = "#F3F3F3"
        SiGlobal.siui.colors["SVG_A"] = SiGlobal.siui.colors["THEME"]

        SiGlobal.siui.colors["THEME_TRANSITION_A"] = "#2abed8"
        SiGlobal.siui.colors["THEME_TRANSITION_B"] = "#2ad98e"

        SiGlobal.siui.colors["TEXT_A"] = "#1f1f2f"
        SiGlobal.siui.colors["TEXT_B"] = Color.transparency(SiGlobal.siui.colors["TEXT_A"], 0.85)
        SiGlobal.siui.colors["TEXT_C"] = Color.transparency(SiGlobal.siui.colors["TEXT_A"], 0.75)
        SiGlobal.siui.colors["TEXT_D"] = Color.transparency(SiGlobal.siui.colors["TEXT_A"], 0.6)
        SiGlobal.siui.colors["TEXT_E"] = Color.transparency(SiGlobal.siui.colors["TEXT_A"], 0.5)

        SiGlobal.siui.colors["SWITCH_DEACTIVATE"] = "#bec1c7"
        SiGlobal.siui.colors["SWITCH_ACTIVATE"] = "#F3F3F3"

        SiGlobal.siui.colors["BUTTON_HOVER"] = Color.transparency(SiGlobal.siui.colors["THEME"], 0.0625)
        SiGlobal.siui.colors["BUTTON_FLASH"] = Color.transparency(SiGlobal.siui.colors["THEME"], 0.43)

        SiGlobal.siui.colors["SIMPLE_BUTTON_BG"] = Color.transparency(SiGlobal.siui.colors["THEME"], 0.6)

        SiGlobal.siui.colors["TOGGLE_BUTTON_OFF_BG"] = Color.transparency(SiGlobal.siui.colors["THEME"], 0)
        SiGlobal.siui.colors["TOGGLE_BUTTON_ON_BG"] = Color.transparency(SiGlobal.siui.colors["THEME"], 0.1)

    SiGlobal.siui.reloadAllWindowsStyleSheet()


# 加载主题颜色
load_colors(is_dark=False)


class SingleSettingOption(SiDenseVContainer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setSpacing(2)

        self.title = SiLabel(self)
        self.title.setFont(SiGlobal.siui.fonts["S_BOLD"])
        self.title.setAutoAdjustSize(True)

        self.description = SiLabel(self)
        self.description.setFont(SiGlobal.siui.fonts["S_NORMAL"])
        self.description.setAutoAdjustSize(True)

        self.addWidget(self.title)
        self.addWidget(self.description)
        self.addPlaceholder(4)

    def setTitle(self, title: str, description: str):
        self.title.setText(title)
        self.description.setText(description)

    def reloadStyleSheet(self):
        super().reloadStyleSheet()

        self.title.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_B"]))
        self.description.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_D"]))


class SingleTODOOption(SiDenseHContainer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setShrinking(True)
        self.task_text = ""
        self.task_date = None

        self.check_box = SiCheckBox(self)
        self.check_box.resize(12, 12)
        self.check_box.setText(" ")
        self.check_box.toggled.connect(self._onChecked)

        self.text_label = SiLabel(self)
        self.text_label.resize(500 - 48 - 48 - 32, 32)
        self.text_label.setWordWrap(True)
        self.text_label.setAutoAdjustSize(True)
        self.text_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.text_label.setFixedStyleSheet("padding-top: 2px; padding-bottom: 2px")

        self.date_label = SiLabel(self)
        self.date_label.setFixedWidth(120)
        self.date_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.date_label.hide()

        self.addWidget(self.check_box)
        self.addWidget(self.text_label)
        self.addWidget(self.date_label, "right")

        self.move = self.moveTo

        # 初始化时自动载入样式表
        self.reloadStyleSheet()

    def reloadStyleSheet(self):
        super().reloadStyleSheet()

        self.text_label.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_B"]))
        self.date_label.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_E"]))

    def _onChecked(self, state):
        if state is True:
            SiGlobal.todo_list.delete_pile.append(self)
        else:
            index = SiGlobal.todo_list.delete_pile.index(self)
            SiGlobal.todo_list.delete_pile.pop(index)

    def setTaskData(self, text: str, task_date: str = None):
        self.task_text = text
        self.task_date = task_date
        self.text_label.setText(text)

        if task_date:
            self.date_label.setText(task_date)
            self.date_label.show()
        else:
            self.date_label.setText("")
            self.date_label.hide()

    def setText(self, text: str):
        self.setTaskData(text=text, task_date=None)

    def getRawEntry(self):
        return build_task_entry(self.task_text, self.task_date)

    def adjustSize(self):
        self.setFixedHeight(self.text_label.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        date_width = self.date_label.width() if self.task_date else 0
        self.text_label.setFixedWidth(max(120, event.size().width() - 48 - date_width))
        self.text_label.adjustSize()
        self.adjustSize()


class AppHeaderPanel(SiLabel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.background_label = SiLabel(self)
        self.background_label.setFixedStyleSheet("border-radius: 8px")

        self.container_h = SiDenseHContainer(self)
        self.container_h.setAlignCenter(True)
        self.container_h.setFixedHeight(48)
        self.container_h.setSpacing(0)

        self.icon = SiSvgLabel(self)
        self.icon.resize(32, 32)
        self.icon.setSvgSize(16, 16)

        self.unfold_button = SiToggleButton(self)
        self.unfold_button.setFixedHeight(32)
        self.unfold_button.attachment().setText("0个待办事项")
        self.unfold_button.setChecked(True)

        self.settings_button = SiToggleButton(self)
        self.settings_button.resize(32, 32)
        self.settings_button.setHint("设置")
        self.settings_button.setChecked(False)

        self.calendar_button = SiToggleButton(self)
        self.calendar_button.resize(32, 32)
        self.calendar_button.setHint("日历")
        self.calendar_button.setChecked(False)
        self.calendar_icon_label = SiPixLabel(self.calendar_button)
        self.calendar_icon_label.resize(18, 18)
        self.calendar_icon_label.setBorderRadius(0)
        self.calendar_button.setAttachment(self.calendar_icon_label)

        self.add_todo_button = SiToggleButton(self)
        self.add_todo_button.resize(32, 32)
        self.add_todo_button.setHint("添加新待办")
        self.add_todo_button.setChecked(False)

        self.container_h.addPlaceholder(16)
        self.container_h.addWidget(self.icon)
        self.container_h.addPlaceholder(4)
        self.container_h.addWidget(self.unfold_button)

        self.container_h.addPlaceholder(16, "right")
        self.container_h.addWidget(self.settings_button, "right")
        self.container_h.addPlaceholder(8, "right")
        self.container_h.addWidget(self.calendar_button, "right")
        self.container_h.addPlaceholder(16, "right")
        self.container_h.addWidget(self.add_todo_button, "right")

        # 按钮加入全局变量
        SiGlobal.todo_list.todo_list_unfold_button = self.unfold_button
        SiGlobal.todo_list.add_todo_unfold_button = self.add_todo_button
        SiGlobal.todo_list.settings_unfold_button = self.settings_button
        SiGlobal.todo_list.calendar_unfold_button = self.calendar_button

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.background_label.resize(event.size().width(), 48)
        self.container_h.resize(event.size().width(), 48)

    def reloadStyleSheet(self):
        super().reloadStyleSheet()
        # 按钮颜色
        self.unfold_button.setStateColor(SiGlobal.siui.colors["TOGGLE_BUTTON_OFF_BG"],
                                         SiGlobal.siui.colors["TOGGLE_BUTTON_ON_BG"])
        self.settings_button.setStateColor(SiGlobal.siui.colors["TOGGLE_BUTTON_OFF_BG"],
                                           SiGlobal.siui.colors["TOGGLE_BUTTON_ON_BG"])
        self.calendar_button.setStateColor(SiGlobal.siui.colors["TOGGLE_BUTTON_OFF_BG"],
                                           SiGlobal.siui.colors["TOGGLE_BUTTON_ON_BG"])
        self.add_todo_button.setStateColor(SiGlobal.siui.colors["TOGGLE_BUTTON_OFF_BG"],
                                           SiGlobal.siui.colors["TOGGLE_BUTTON_ON_BG"])

        # svg 图标
        self.settings_button.attachment().load(SiGlobal.siui.icons["fi-rr-menu-burger"])
        if os.path.exists(CALENDAR_ICON_IMAGE_PATH):
            self.calendar_icon_label.load(CALENDAR_ICON_IMAGE_PATH)
        # else: keep current icon as-is
        self.add_todo_button.attachment().load(SiGlobal.siui.icons["fi-rr-apps-add"])
        self.icon.load('<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" id="Layer_1" '
                       'data-name="Layer 1" viewBox="0 0 24 24" width="512" height="512"><path d="M0,8v-1C0,4.243,'
                       '2.243,2,5,2h1V1c0-.552,.447-1,1-1s1,.448,1,1v1h8V1c0-.552,.447-1,1-1s1,.448,1,1v1h1c2.757,0,'
                       '5,2.243,5,5v1H0Zm24,2v9c0,2.757-2.243,5-5,5H5c-2.757,0-5-2.243-5-5V10H24Zm-6.168,'
                       '3.152c-.384-.397-1.016-.409-1.414-.026l-4.754,4.582c-.376,.376-1.007,'
                       '.404-1.439-.026l-2.278-2.117c-.403-.375-1.035-.354-1.413,.052-.376,.404-.353,1.037,.052,'
                       '1.413l2.252,2.092c.566,.567,1.32,.879,2.121,.879s1.556-.312,2.108-.866l4.74-4.568c.397-.383,'
                       '.409-1.017,.025-1.414Z" fill="{}" /></svg>'.format(SiGlobal.siui.colors["SVG_A"]).encode())

        self.background_label.setStyleSheet("""background-color: {}; border: 1px solid {}""".format(
            SiGlobal.siui.colors["BACKGROUND_COLOR"], SiGlobal.siui.colors["BORDER_COLOR"]))
        self.unfold_button.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_B"]))


class TODOListPanel(ThemedOptionCardPlane):
    todoAmountChanged = pyqtSignal(int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setTitle("全部待办")
        self.setUseSignals(True)

        self.no_todo_label = SiLabel(self)
        self.no_todo_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.no_todo_label.setAutoAdjustSize(True)
        self.no_todo_label.setText("当前没有待办哦")
        self.no_todo_label.setAlignment(Qt.AlignCenter)
        self.no_todo_label.hide()

        self.body().setUseMoveTo(False)
        self.body().setShrinking(True)
        self.body().setAdjustWidgetsSize(True)

        self.footer().setFixedHeight(64)
        self.footer().setSpacing(8)
        self.footer().setAlignCenter(True)

        self.complete_all_button = SiSimpleButton(self)
        self.complete_all_button.resize(32, 32)
        self.complete_all_button.setHint("全部完成")
        self.complete_all_button.clicked.connect(self._onCompleteAllButtonClicked)

        self.footer().addWidget(self.complete_all_button, "right")

        # 全局方法
        SiGlobal.todo_list.addTODO = self.addTODO

    def updateTODOAmount(self):
        todo_amount = len(self.body().widgets_top)
        self.todoAmountChanged.emit(todo_amount)

        if todo_amount == 0:
            self.no_todo_label.show()
        else:
            self.no_todo_label.hide()

    def reloadStyleSheet(self):
        self.setThemeColor(SiGlobal.siui.colors["PANEL_THEME"])
        super().reloadStyleSheet()

        self.no_todo_label.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_E"]))
        self.complete_all_button.attachment().load(SiGlobal.siui.icons["fi-rr-list-check"])

    def _onCompleteAllButtonClicked(self):
        for obj in self.body().widgets_top:
            if isinstance(obj, SingleTODOOption):
                obj.check_box.setChecked(True)

    def addTODO(self, text, task_date=None):
        new_todo = SingleTODOOption(self)
        self.body().addWidget(new_todo)

        new_todo.setTaskData(text=text, task_date=task_date)
        new_todo.show()
        new_todo.adjustSize()

        SiGlobal.todo_list.todo_list_unfold_button.setChecked(True)
        self.adjustSize()
        self.updateTODOAmount()
        if hasattr(SiGlobal.todo_list, "refresh_calendar"):
            SiGlobal.todo_list.refresh_calendar()

    def getRawTodos(self):
        entries = []
        for widget in self.body().widgets_top:
            if isinstance(widget, SingleTODOOption):
                entries.append(widget.getRawEntry())
        return entries

    def getDailyTasks(self, date_key: str):
        tasks = []
        for widget in self.body().widgets_top:
            if isinstance(widget, SingleTODOOption) and widget.task_date == date_key:
                tasks.append(widget.task_text)
        return tasks

    def getScheduledTasks(self):
        tasks = []
        for widget in self.body().widgets_top:
            if isinstance(widget, SingleTODOOption) and widget.task_date:
                tasks.append((widget.task_date, widget.task_text))
        tasks.sort(key=lambda item: item[0])
        return tasks

    def addTaskForDate(self, date_key: str, task_text: str):
        parsed_date = QDate.fromString(date_key, "yyyy-MM-dd")
        if (not parsed_date.isValid()) or parsed_date < QDate.currentDate():
            return False

        normalized_text = canonicalize_calendar_task_text(task_text)
        if normalized_text == "":
            return False
        self.addTODO(normalized_text, date_key)
        return True

    def removeTask(self, date_key: str, task_text: str, occurrence_index: int = 0):
        matched_widgets = []
        for widget in list(self.body().widgets_top):
            if not isinstance(widget, SingleTODOOption):
                continue
            if widget.task_date == date_key and widget.task_text == task_text:
                matched_widgets.append(widget)

        if occurrence_index < 0 or occurrence_index >= len(matched_widgets):
            return False

        target_widget = matched_widgets[occurrence_index]
        if target_widget in SiGlobal.todo_list.delete_pile:
            SiGlobal.todo_list.delete_pile.remove(target_widget)

        self.body().removeWidget(target_widget)
        target_widget.close()
        self.adjustSize()
        self.updateTODOAmount()

        if hasattr(SiGlobal.todo_list, "refresh_calendar"):
            SiGlobal.todo_list.refresh_calendar()
        return True

    def adjustSize(self):
        self.body().adjustSize()
        super().adjustSize()

    def leaveEvent(self, event):
        super().leaveEvent(event)

        for index, obj in enumerate(SiGlobal.todo_list.delete_pile):
            self.body().removeWidget(obj)
            obj.close()

        SiGlobal.todo_list.delete_pile = []

        if SiGlobal.todo_list.todo_list_unfold_button.isChecked() is True:
            self.adjustSize()
            self.updateTODOAmount()
        if hasattr(SiGlobal.todo_list, "refresh_calendar"):
            SiGlobal.todo_list.refresh_calendar()

    def showEvent(self, a0):
        super().showEvent(a0)
        self.updateTODOAmount()
        self.setForceUseAnimations(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.no_todo_label.resize(event.size().width(), 150)


class AddNewTODOPanel(ThemedOptionCardPlane):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setTitle("\u6dfb\u52a0\u5f85\u529e")
        self.setUseSignals(True)

        self.confirm_button = SiSimpleButton(self)
        self.confirm_button.resize(32, 32)
        self.confirm_button.setHint("\u786e\u8ba4\u6dfb\u52a0")

        self.cancel_button = SiSimpleButton(self)
        self.cancel_button.resize(32, 32)
        self.cancel_button.setHint("\u53d6\u6d88")

        self.header().addWidget(self.cancel_button, "right")
        self.header().addWidget(self.confirm_button, "right")

        self.instruction = SiLabel(self)
        self.instruction.setFont(SiGlobal.siui.fonts["S_BOLD"])
        self.instruction.setText("\u8f93\u5165\u5f85\u529e\u5185\u5bb9")

        self.text_edit = QTextEdit(self)
        self.text_edit.setFixedHeight(70)
        self.text_edit.setFont(SiGlobal.siui.fonts["S_NORMAL"])
        self.text_edit.lineWrapMode()

        self.day_plan_instruction = SiLabel(self)
        self.day_plan_instruction.setFont(SiGlobal.siui.fonts["S_BOLD"])
        self.day_plan_instruction.setText("\u8bbe\u4e3a\u65e5\u8ba1\u5212\uff08\u7ed1\u5b9a\u65e5\u671f\uff09")

        self.enable_day_plan = SiSwitch(self)
        self.enable_day_plan.setFixedHeight(28)
        self.enable_day_plan.setChecked(self._savedDayPlanEnabled())

        self.date_edit = QDateEdit(self)
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setMinimumDate(QDate.currentDate())
        self.date_edit.setFixedHeight(34)
        self.date_edit.setFixedWidth(220)
        self.date_edit.setFont(SiGlobal.siui.fonts["S_NORMAL"])

        self.date_rule_hint = SiLabel(self)
        self.date_rule_hint.setText("\u4ec5\u652f\u6301\u4eca\u5929\u53ca\u4e4b\u540e\u65e5\u671f")

        self.feedback_label = SiLabel(self)
        self.feedback_label.setWordWrap(True)
        self.feedback_label.setText("")

        self.enable_day_plan.toggled.connect(self.date_edit.setEnabled)
        self.enable_day_plan.toggled.connect(self._onDayPlanToggled)
        self.date_edit.setEnabled(self.enable_day_plan.isChecked())

        self._date_refresh_timer = QTimer(self)
        self._date_refresh_timer.setInterval(60 * 1000)
        self._date_refresh_timer.timeout.connect(self.refreshDateForCurrentDay)
        self._date_refresh_timer.start()

        self.body().setAdjustWidgetsSize(True)
        self.body().setSpacing(4)
        self.body().addWidget(self.instruction)
        self.body().addWidget(self.text_edit)
        self.body().addWidget(self.day_plan_instruction)
        self.body().addWidget(self.enable_day_plan)
        self.body().addWidget(self.date_edit)
        self.body().addWidget(self.date_rule_hint)
        self.body().addWidget(self.feedback_label)

    @staticmethod
    def _savedDayPlanEnabled():
        value = SiGlobal.todo_list.settings_parser.options.get("ADD_TODO_DAY_PLAN_ENABLED", True)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def _onDayPlanToggled(self, state):
        SiGlobal.todo_list.settings_parser.modify("ADD_TODO_DAY_PLAN_ENABLED", bool(state))
        SiGlobal.todo_list.settings_parser.write()

    def refreshDateForCurrentDay(self, reset_to_today=False):
        today = QDate.currentDate()
        self.date_edit.setMinimumDate(today)
        if reset_to_today or self.date_edit.date() < today:
            self.date_edit.setDate(today)

    def prepareForOpen(self):
        self.enable_day_plan.setChecked(self._savedDayPlanEnabled())
        self.refreshDateForCurrentDay(reset_to_today=True)

    def getTaskDate(self):
        if self.enable_day_plan.isChecked():
            return self.date_edit.date().toString("yyyy-MM-dd")
        return None

    def resetForm(self):
        self.text_edit.setText("")
        self.enable_day_plan.setChecked(self._savedDayPlanEnabled())
        self.refreshDateForCurrentDay(reset_to_today=True)
        self.feedback_label.setText("")

    def setFeedback(self, message: str, is_error: bool = False):
        if is_error:
            self.feedback_label.setStyleSheet("color: #d9534f")
        else:
            self.feedback_label.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_D"]))
        self.feedback_label.setText(message)

    def adjustSize(self):
        self.resize(self.width(), 300)

    def reloadStyleSheet(self):
        self.setThemeColor(SiGlobal.siui.colors["PANEL_THEME"])
        super().reloadStyleSheet()

        self.confirm_button.attachment().load(SiGlobal.siui.icons["fi-rr-check"])
        self.cancel_button.attachment().load(SiGlobal.siui.icons["fi-rr-cross"])
        self.instruction.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_B"]))
        self.day_plan_instruction.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_B"]))
        self.date_rule_hint.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_D"]))
        if self.feedback_label.text().strip() == "":
            self.feedback_label.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_D"]))
        self.text_edit.setStyleSheet(
            """
            border: 1px solid {};
            background-color: {};
            border-radius: 4px;
            padding-left: 8px; padding-right: 8px;
            color: {}
            """.format(SiGlobal.siui.colors["BORDER_COLOR"],
                       SiGlobal.siui.colors["BACKGROUND_DARK_COLOR"],
                       SiGlobal.siui.colors["TEXT_B"])
        )
        self.date_edit.setStyleSheet(
            """
            border: 1px solid {};
            background-color: {};
            border-radius: 4px;
            padding-left: 8px; padding-right: 8px;
            color: {}
            """.format(SiGlobal.siui.colors["BORDER_COLOR"],
                       SiGlobal.siui.colors["BACKGROUND_DARK_COLOR"],
                       SiGlobal.siui.colors["TEXT_B"])
        )

    def showEvent(self, a0):
        super().showEvent(a0)
        self.setForceUseAnimations(True)


class CalendarEventDialog(QDialog):
    WEEKDAY_LABELS = {
        1: "周一",
        2: "周二",
        3: "周三",
        4: "周四",
        5: "周五",
        6: "周六",
        7: "周日",
    }

    def __init__(self, parent, date_key: str, task_provider, task_creator, task_remover):
        super().__init__(parent)
        self.date_key = date_key
        self._task_provider = task_provider
        self._task_creator = task_creator
        self._task_remover = task_remover
        self._editing_target = None

        self.setModal(True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setWindowTitle("新建任务")
        self.setMinimumWidth(560)
        self.resize(600, 670)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 14, 18, 14)
        root_layout.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        self.dialog_title_label = QLabel("新建任务", self)
        self.dialog_title_label.setStyleSheet("font-size: 20px; font-weight: 800;")
        header_row.addWidget(self.dialog_title_label, 1)

        self.close_button = QPushButton("×", self)
        self.close_button.setFixedSize(30, 30)
        self.close_button.clicked.connect(self.reject)
        header_row.addWidget(self.close_button, 0, Qt.AlignRight)
        root_layout.addLayout(header_row)

        self.header_divider = QFrame(self)
        self.header_divider.setFrameShape(QFrame.HLine)
        self.header_divider.setLineWidth(1)
        root_layout.addWidget(self.header_divider)

        self.date_label = QLabel(self._friendlyDateText(), self)
        self.date_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        root_layout.addWidget(self.date_label)

        self.middle_divider = QFrame(self)
        self.middle_divider.setFrameShape(QFrame.HLine)
        self.middle_divider.setLineWidth(1)
        root_layout.addWidget(self.middle_divider)

        self.today_tasks_label = QLabel("当天任务", self)
        root_layout.addWidget(self.today_tasks_label)

        self.tasks_card = QFrame(self)
        self.tasks_card_layout = QVBoxLayout(self.tasks_card)
        self.tasks_card_layout.setContentsMargins(10, 10, 10, 10)
        self.tasks_card_layout.setSpacing(8)

        self.event_list = QListWidget(self.tasks_card)
        self.event_list.setFixedHeight(170)
        self.tasks_card_layout.addWidget(self.event_list)

        self.empty_state_label = QLabel("📭\n这天还没有任务", self.tasks_card)
        self.empty_state_label.setAlignment(Qt.AlignCenter)
        self.empty_state_label.setFixedHeight(170)
        self.tasks_card_layout.addWidget(self.empty_state_label)
        root_layout.addWidget(self.tasks_card)

        self.form_divider = QFrame(self)
        self.form_divider.setFrameShape(QFrame.HLine)
        self.form_divider.setLineWidth(1)
        root_layout.addWidget(self.form_divider)

        self.new_task_label = QLabel("新建任务", self)
        root_layout.addWidget(self.new_task_label)

        self.title_caption = QLabel("任务标题", self)
        root_layout.addWidget(self.title_caption)
        self.title_input = QLineEdit(self)
        self.title_input.setPlaceholderText("输入任务内容...")
        root_layout.addWidget(self.title_input)

        self.description_caption = QLabel("描述 (可选)", self)
        root_layout.addWidget(self.description_caption)
        self.description_input = QTextEdit(self)
        self.description_input.setPlaceholderText("添加详细说明...")
        self.description_input.setFixedHeight(82)
        root_layout.addWidget(self.description_input)

        option_head_row = QHBoxLayout()
        option_head_row.setSpacing(12)
        self.tag_label = QLabel("任务标签", self)
        self.time_label = QLabel("时间", self)
        option_head_row.addWidget(self.tag_label, 1)
        option_head_row.addWidget(self.time_label, 1)
        root_layout.addLayout(option_head_row)

        option_value_row = QHBoxLayout()
        option_value_row.setSpacing(12)
        self.category_combo = QComboBox(self)
        self.category_combo.addItem("普通事件", "normal")
        self.category_combo.addItem("重要事件", "important")
        self.category_combo.addItem("纪念日", "anniversary")
        self.time_combo = QComboBox(self)
        self.time_combo.addItems(["全天", "09:00", "14:00", "18:00", "20:00"])
        option_value_row.addWidget(self.category_combo, 1)
        option_value_row.addWidget(self.time_combo, 1)
        root_layout.addLayout(option_value_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addStretch(1)
        self.delete_button = QPushButton("删除任务", self)
        self.delete_button.clicked.connect(self._onDeleteClicked)
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.clicked.connect(self._onCancelClicked)
        self.save_button = QPushButton("创建", self)
        self.save_button.clicked.connect(self._onSaveClicked)
        action_row.addWidget(self.delete_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.save_button)
        root_layout.addLayout(action_row)

        self.feedback_label = QLabel("", self)
        self.feedback_label.setWordWrap(True)
        root_layout.addWidget(self.feedback_label)

        self.title_input.returnPressed.connect(self._onSaveClicked)
        self.event_list.itemDoubleClicked.connect(self._onTaskDoubleClicked)
        self.event_list.itemSelectionChanged.connect(self._onSelectionChanged)

        self._reloadStyle()
        self._setModeCreate(reset_fields=True)
        self._refreshEventList()

    def _friendlyDateText(self):
        parsed = QDate.fromString(self.date_key, "yyyy-MM-dd")
        if not parsed.isValid():
            return self.date_key
        week_text = self.WEEKDAY_LABELS.get(parsed.dayOfWeek(), "")
        return f"{parsed.month()}月{parsed.day()}日 · {week_text}"

    @staticmethod
    def _splitTimeFromDescription(description_text: str):
        desc = (description_text or "").strip()
        match = re.match(r"^\[(\d{2}:\d{2})\]\s*(.*)$", desc, re.S)
        if match:
            return match.group(1), match.group(2).strip()
        return "全天", desc

    def _composeDescription(self):
        selected_time = self.time_combo.currentText().strip()
        desc = self.description_input.toPlainText().strip()
        if selected_time and selected_time != "全天":
            if desc:
                return f"[{selected_time}] {desc}"
            return f"[{selected_time}]"
        return desc

    def _reloadStyle(self):
        border_color = SiGlobal.siui.colors["BORDER_COLOR"]
        bg_color = SiGlobal.siui.colors["BACKGROUND_COLOR"]
        card_bg = SiGlobal.siui.colors["BACKGROUND_DARK_COLOR"]
        text_color = SiGlobal.siui.colors["TEXT_B"]
        subtitle_color = SiGlobal.siui.colors["TEXT_D"]
        theme_color = SiGlobal.siui.colors["PANEL_THEME"]

        self.setStyleSheet(
            """
            QDialog { background: %s; color: %s; }
            QLabel { color: %s; }
            QLineEdit, QTextEdit, QComboBox {
                border: 1px solid %s;
                border-radius: 6px;
                background: #ffffff;
                color: #1f2937;
                padding: 6px;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
                border: 1px solid %s;
            }
            QListWidget {
                border: none;
                background: transparent;
                color: %s;
                padding: 2px;
            }
            QPushButton {
                border: 1px solid %s;
                border-radius: 6px;
                background: %s;
                color: %s;
                padding: 6px 12px;
            }
            """
            % (
                bg_color,
                text_color,
                text_color,
                border_color,
                theme_color,
                text_color,
                border_color,
                card_bg,
                text_color,
            )
        )

        self.close_button.setStyleSheet(
            """
            QPushButton {
                border: 1px solid %s;
                border-radius: 15px;
                background: #ffffff;
                color: #374151;
                font-size: 20px;
                font-weight: 700;
                padding: 0;
            }
            QPushButton:hover {
                background: #fef2f2;
                color: #b91c1c;
            }
            """
            % border_color
        )
        self.header_divider.setStyleSheet("color: {}".format(border_color))
        self.middle_divider.setStyleSheet("color: {}".format(border_color))
        self.form_divider.setStyleSheet("color: {}".format(border_color))
        self.tasks_card.setStyleSheet(
            "QFrame { background: %s; border: 1px solid %s; border-radius: 8px; }" % (card_bg, border_color)
        )
        self.empty_state_label.setStyleSheet("color: {}; font-size: 14px;".format(subtitle_color))
        self.feedback_label.setStyleSheet("color: {};".format(subtitle_color))
        self.today_tasks_label.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.new_task_label.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.tag_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.time_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.title_caption.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.description_caption.setStyleSheet("font-size: 13px; font-weight: 600;")

        self.save_button.setStyleSheet(
            """
            QPushButton {
                border: 1px solid %s;
                border-radius: 6px;
                background: %s;
                color: #ffffff;
                font-weight: 700;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background: #0f75be;
            }
            """
            % (theme_color, theme_color)
        )
        self.cancel_button.setStyleSheet(
            """
            QPushButton {
                border: 1px solid %s;
                border-radius: 6px;
                background: #ffffff;
                color: #374151;
                padding: 6px 14px;
            }
            """
            % border_color
        )
        self.delete_button.setStyleSheet(
            """
            QPushButton {
                border: 1px solid #ef4444;
                border-radius: 6px;
                background: #ffffff;
                color: #ef4444;
                padding: 6px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #fef2f2;
            }
            """
        )

    def _setFeedback(self, message: str, is_error: bool = False):
        if is_error:
            self.feedback_label.setStyleSheet("color: #d9534f;")
        else:
            self.feedback_label.setStyleSheet("color: {};".format(SiGlobal.siui.colors["TEXT_D"]))
        self.feedback_label.setText(message)

    def _setModeCreate(self, reset_fields: bool):
        self._editing_target = None
        self.dialog_title_label.setText("新建任务")
        self.setWindowTitle("新建任务")
        self.save_button.setText("创建")
        self.delete_button.hide()
        if reset_fields:
            self.title_input.clear()
            self.description_input.clear()
            self.category_combo.setCurrentIndex(0)
            self.time_combo.setCurrentIndex(0)

    def _setModeEdit(self, parsed_task, target_tuple):
        self._editing_target = target_tuple
        self.dialog_title_label.setText("编辑任务")
        self.setWindowTitle("编辑任务")
        self.save_button.setText("保存")
        self.delete_button.show()

        self.title_input.setText(parsed_task.get("title", ""))
        time_text, pure_desc = self._splitTimeFromDescription(parsed_task.get("description", ""))
        self.description_input.setPlainText(pure_desc)
        tag_type = parsed_task.get("type", "normal")
        self.category_combo.setCurrentIndex(max(0, self.category_combo.findData(tag_type)))
        time_index = self.time_combo.findText(time_text)
        self.time_combo.setCurrentIndex(time_index if time_index >= 0 else 0)

    def _refreshEventList(self):
        self.event_list.clear()
        tasks = self._task_provider(self.date_key)
        occurrence_counter = {}
        for task_text in tasks:
            parsed_task = parse_calendar_event_text(task_text)
            time_text, pure_desc = self._splitTimeFromDescription(parsed_task.get("description", ""))
            tag_label = parsed_task.get("label", "普通")
            display_text = f"[{tag_label}] {parsed_task.get('title', '')}"
            if time_text != "全天":
                display_text += f"  {time_text}"
            if pure_desc:
                display_text += f"  |  {pure_desc}"

            item = QListWidgetItem(display_text)
            if parsed_task["type"] == "important":
                item.setForeground(QColor("#d97706"))
            elif parsed_task["type"] == "anniversary":
                item.setForeground(QColor("#db2777"))
            else:
                item.setForeground(QColor("#1f4f7a"))

            raw_text = parsed_task["raw_text"]
            occurrence_index = occurrence_counter.get(raw_text, 0)
            occurrence_counter[raw_text] = occurrence_index + 1
            item.setData(Qt.UserRole, (raw_text, occurrence_index))
            item.setData(Qt.UserRole + 1, parsed_task)
            self.event_list.addItem(item)

        has_tasks = self.event_list.count() > 0
        self.event_list.setVisible(has_tasks)
        self.empty_state_label.setVisible(not has_tasks)
        if not has_tasks:
            self._setFeedback("")

    def _onSelectionChanged(self):
        selected_items = self.event_list.selectedItems()
        if len(selected_items) == 0:
            return
        self._setFeedback("双击任务可进入编辑模式")

    def _onTaskDoubleClicked(self, item):
        if item is None:
            return
        parsed_task = item.data(Qt.UserRole + 1) or {}
        target_tuple = item.data(Qt.UserRole)
        if target_tuple is None:
            return
        self._setModeEdit(parsed_task, target_tuple)

    def _onCancelClicked(self):
        self.reject()

    def _onSaveClicked(self):
        parsed_date = QDate.fromString(self.date_key, "yyyy-MM-dd")
        if (not parsed_date.isValid()) or parsed_date < QDate.currentDate():
            self._setFeedback("不能添加过去日期的计划", is_error=True)
            return

        title = self.title_input.text().strip()
        if title == "":
            self._setFeedback("请先输入任务标题", is_error=True)
            return

        event_type = self.category_combo.currentData() or "normal"
        description = self._composeDescription()
        encoded_text = encode_calendar_event(title, event_type, description)

        if self._editing_target is None:
            if self._task_creator(self.date_key, encoded_text) is False:
                self._setFeedback("创建失败，请重试", is_error=True)
                return
            self._setFeedback("已创建任务")
            self._setModeCreate(reset_fields=True)
            self._refreshEventList()
            return

        old_text, old_occurrence = self._editing_target
        if self._task_remover(self.date_key, old_text, old_occurrence) is False:
            self._setFeedback("保存失败：无法删除旧任务", is_error=True)
            return
        if self._task_creator(self.date_key, encoded_text) is False:
            self._task_creator(self.date_key, old_text)
            self._setFeedback("保存失败：无法写入新任务", is_error=True)
            return
        self._setFeedback("已保存修改")
        self._setModeCreate(reset_fields=True)
        self._refreshEventList()

    def _onDeleteClicked(self):
        if self._editing_target is None:
            self._setFeedback("请先双击某条任务进入编辑模式", is_error=True)
            return
        old_text, old_occurrence = self._editing_target
        if self._task_remover(self.date_key, old_text, old_occurrence) is False:
            self._setFeedback("删除失败，请重试", is_error=True)
            return
        self._setFeedback("已删除任务")
        self._setModeCreate(reset_fields=True)
        self._refreshEventList()

class CalendarDayCell(QWidget):
    clicked = pyqtSignal(int)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(92)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._hovered = False
        self._base_bg = "#ffffff"
        self._base_border = "#d6dbe3"
        self._is_selected = False
        self._in_current_month = True

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(3)

        self.day_label = QLabel(self)
        self.day_label.setFixedHeight(28)
        self.day_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.day_label.setAlignment(Qt.AlignCenter)
        root_layout.addWidget(self.day_label)

        self.event_labels = []
        for _ in range(2):
            event_label = QLabel(self)
            event_label.setMinimumHeight(16)
            event_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            event_label.hide()
            self.event_labels.append(event_label)
            root_layout.addWidget(event_label)

        self.more_label = QLabel(self)
        self.more_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.more_label.hide()
        root_layout.addWidget(self.more_label)

        root_layout.addStretch(1)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.index)

    def enterEvent(self, event):
        super().enterEvent(event)
        self._hovered = True
        self._applyContainerStyle()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hovered = False
        self._applyContainerStyle()

    def _applyContainerStyle(self):
        bg = self._base_bg
        border = "#d6dbe3"
        if self._hovered and (not self._is_selected):
            if self._in_current_month:
                bg = "#f6fbff"
            else:
                bg = "#f1f4f7"

        column = self.index % 7
        row = self.index // 7
        left_width = 1 if column == 0 else 0
        top_width = 1 if row == 0 else 0

        self.setStyleSheet(
            f"background:{bg};"
            f"border-left:{left_width}px solid {border};"
            f"border-right:1px solid {border};"
            f"border-top:{top_width}px solid {border};"
            f"border-bottom:1px solid {border};"
            "border-radius:0px;"
        )

    def _eventBadgeStyle(self, event_type: str):
        if event_type == "important":
            return "background:#ffeaa7;color:#d35400;border:1px solid #ffd36b;border-radius:8px;padding:1px 6px;font-size:16px;font-weight:600;"
        if event_type == "anniversary":
            return "background:#fab1a0;color:#e84393;border:1px solid #f59cbe;border-radius:8px;padding:1px 6px;font-size:16px;font-weight:600;"
        return "background:#e9ecef;color:#4b5563;border:1px solid #dde1e6;border-radius:8px;padding:1px 6px;font-size:16px;font-weight:600;"

    def setContent(self, *, date_obj: QDate, in_current_month: bool, is_today: bool, is_selected: bool, is_weekend: bool, event_infos):
        bg_color = "#ffffff"
        text_color = "#2f3640"
        border_color = "#d6dbe3"

        if not in_current_month:
            bg_color = "#f8f9fa"
            text_color = "#adb5bd"

        if is_weekend and in_current_month:
            bg_color = "#fff6f6"
            text_color = "#e74c3c"

        if len(event_infos) > 0 and in_current_month:
            if is_weekend:
                bg_color = "#fff1f1"
            else:
                bg_color = "#ffffff"

        if is_today:
            pass

        if is_selected:
            bg_color = "#ffffff"
            border_color = "#d6dbe3"

        self._base_bg = bg_color
        self._base_border = border_color
        self._is_selected = is_selected
        self._in_current_month = in_current_month
        self._applyContainerStyle()
        self.day_label.setText(str(date_obj.day()))
        day_style = f"color:{text_color};font-weight:700;font-size:15px;border:none;background:transparent;"
        if is_today:
            day_style = "color:#ffffff;font-weight:800;font-size:15px;background:#2f9df4;border-radius:14px;border:1px solid #2f9df4;"
        self.day_label.setStyleSheet(day_style)
        for label in self.event_labels:
            label.hide()
            label.setText("")

        for i, event_info in enumerate(event_infos[:2]):
            event_type = event_info.get("type", "normal")
            event_label = event_info.get("label", "普通")
            title = event_info.get("title", "")
            if len(title) > 7:
                title = title[:7] + "..."
            self.event_labels[i].setText(f"[{event_label}] {title}")
            self.event_labels[i].setStyleSheet(self._eventBadgeStyle(event_type))
            self.event_labels[i].show()

        if len(event_infos) > 2:
            self.more_label.setText(f"+{len(event_infos) - 2} more")
            self.more_label.setStyleSheet("color:#6b7280;font-size:12px;border:none;background:transparent;")
            self.more_label.show()
        else:
            self.more_label.hide()


class CalendarTaskCard(QWidget):
    def __init__(self, parsed_task, parent=None):
        super().__init__(parent)
        self.parsed_task = parsed_task
        self._is_selected = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.type_bar = QWidget(self)
        self.type_bar.setFixedWidth(4)
        self.type_bar.setObjectName("type_bar")
        layout.addWidget(self.type_bar)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(3)

        title_text = parsed_task.get("title", "").strip() or "\u672a\u547d\u540d\u8ba1\u5212"
        if parsed_task.get("structured"):
            title_text = f"[{parsed_task.get('label', '\u666e\u901a')}] {title_text}"

        self.title_label = QLabel(title_text, self)
        self.title_label.setWordWrap(True)
        text_col.addWidget(self.title_label)

        description_text = parsed_task.get("description", "").strip()
        self.desc_label = QLabel(description_text, self)
        self.desc_label.setWordWrap(True)
        text_col.addWidget(self.desc_label)
        self.desc_label.setVisible(description_text != "")

        layout.addLayout(text_col, 1)
        self._applyStyle()

    def _applyStyle(self):
        task_type = self.parsed_task.get("type", "normal")
        if task_type == "important":
            bar = "#f59e0b"
            bg = "#fff7e7"
            border = "#f5d08a"
            title = "#9a5800"
        elif task_type == "anniversary":
            bar = "#ec4899"
            bg = "#fff1f8"
            border = "#f7bfde"
            title = "#b03273"
        else:
            bar = "#38bdf8"
            bg = "#f2f8ff"
            border = "#d6e9fb"
            title = "#1f4f7a"

        if self._is_selected:
            border = "#2f9df4"
            bg = "#e8f4ff"

        self.setStyleSheet(
            """
            QWidget {
                background: %s;
                border: 1px solid %s;
                border-radius: 8px;
            }
            """
            % (bg, border)
        )
        self.type_bar.setStyleSheet(
            "background: {}; border-radius: 2px; border: none;".format(bar)
        )
        self.title_label.setStyleSheet(
            "color: {}; font-weight: 700; font-size: 12px; border: none; background: transparent;".format(title)
        )
        self.desc_label.setStyleSheet(
            "color: #5f6b77; font-size: 11px; border: none; background: transparent;"
        )

    def setSelectedState(self, selected: bool):
        self._is_selected = selected
        self._applyStyle()

    def sizeHint(self):
        description_text = self.parsed_task.get("description", "").strip()
        if description_text:
            return QSize(340, 52)
        return QSize(340, 40)


class CalendarPanel(ThemedOptionCardPlane):
    WEEKDAY_LABELS = ["\u5468\u4e00", "\u5468\u4e8c", "\u5468\u4e09", "\u5468\u56db", "\u5468\u4e94", "\u5468\u516d", "\u5468\u65e5"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setTitle("\u65e5\u5386")
        self.setUseSignals(True)

        self._task_provider = lambda _date_key: []
        self._all_tasks_provider = lambda: []
        self._task_creator = lambda _date_key, _task_text: False
        self._task_remover = lambda _date_key, _task_text, _occurrence_index: False
        self._feishu_import_handler = lambda: {"ok": False, "message": "未配置导入处理器"}

        self.selected_date = QDate.currentDate()
        self.current_month_date = QDate(self.selected_date.year(), self.selected_date.month(), 1)
        self._visible_dates = []

        self.overview_label = SiLabel(self)
        self.overview_label.setFont(SiGlobal.siui.fonts["S_BOLD"])

        self.upcoming_tasks_label = QTextEdit(self)
        self.upcoming_tasks_label.setReadOnly(True)
        self.upcoming_tasks_label.setFixedHeight(132)

        self.nav_prev_button = QToolButton(self)
        self.nav_prev_button.setFixedSize(44, 44)
        self.nav_prev_button.setToolTip("\u4e0a\u4e2a\u6708")

        self.nav_next_button = QToolButton(self)
        self.nav_next_button.setFixedSize(44, 44)
        self.nav_next_button.setToolTip("\u4e0b\u4e2a\u6708")

        self.nav_today_button = SiSimpleButton(self)
        self.nav_today_button.setFixedHeight(32)
        self.nav_today_button.attachment().setText("\u4eca")
        self.nav_today_button.adjustSize()

        self.import_feishu_button = SiSimpleButton(self)
        self.import_feishu_button.setFixedHeight(32)
        self.import_feishu_button.attachment().setText("\u5bfc\u5165\u98de\u4e66")
        self.import_feishu_button.adjustSize()

        self.month_label = SiLabel(self)
        self.month_label.setFont(SiGlobal.siui.fonts["S_BOLD"])
        self.month_label.setAlignment(Qt.AlignCenter)
        self.month_label.setAutoAdjustSize(True)
        self.month_label.setFixedWidth(190)

        self.nav_row = SiDenseHContainer(self)
        self.nav_row.setFixedHeight(60)
        self.nav_row.setAlignCenter(True)
        self.nav_row.setSpacing(8)
        self.nav_row.addWidget(self.nav_prev_button)
        self.nav_row.addPlaceholder(6)
        self.nav_row.addWidget(self.month_label)
        self.nav_row.addPlaceholder(6, "right")
        self.nav_row.addWidget(self.nav_next_button, "right")
        self.nav_row.addPlaceholder(8, "right")
        self.nav_row.addWidget(self.nav_today_button, "right")
        self.nav_row.addPlaceholder(8, "right")
        self.nav_row.addWidget(self.import_feishu_button, "right")

        self.weekday_header = QWidget(self)
        self.weekday_layout = QGridLayout(self.weekday_header)
        self.weekday_layout.setContentsMargins(0, 0, 0, 0)
        self.weekday_layout.setHorizontalSpacing(0)
        self.weekday_layout.setVerticalSpacing(0)
        self.weekday_header.setFixedHeight(66)
        self.weekday_labels = []
        for index, label in enumerate(self.WEEKDAY_LABELS):
            weekday_label = QLabel(label, self.weekday_header)
            weekday_label.setAlignment(Qt.AlignCenter)
            weekday_label.setFixedHeight(66)
            self.weekday_labels.append(weekday_label)
            self.weekday_layout.addWidget(weekday_label, 0, index)

        self.days_grid_widget = QWidget(self)
        self.days_grid_layout = QGridLayout(self.days_grid_widget)
        self.days_grid_layout.setContentsMargins(0, 0, 0, 0)
        self.days_grid_layout.setHorizontalSpacing(0)
        self.days_grid_layout.setVerticalSpacing(0)
        self.days_grid_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.days_grid_widget.setFixedHeight(624)
        for row in range(6):
            self.days_grid_layout.setRowMinimumHeight(row, 92)
            self.days_grid_layout.setRowStretch(row, 1)
        for column in range(7):
            self.days_grid_layout.setColumnStretch(column, 1)
        self.day_cells = []

        for index in range(42):
            day_cell = CalendarDayCell(index, self.days_grid_widget)
            day_cell.clicked.connect(self._onDayCellClicked)
            self.day_cells.append(day_cell)
            self.days_grid_layout.addWidget(day_cell, index // 7, index % 7)

        self.calendar_grid_block = QWidget(self)
        self.calendar_grid_layout = QVBoxLayout(self.calendar_grid_block)
        self.calendar_grid_layout.setContentsMargins(0, 0, 0, 0)
        self.calendar_grid_layout.setSpacing(0)
        self.calendar_grid_layout.addWidget(self.weekday_header)
        self.calendar_grid_layout.addWidget(self.days_grid_widget)

        self.selected_date_label = SiLabel(self)
        self.selected_date_label.setFont(SiGlobal.siui.fonts["S_BOLD"])

        self.quick_add_input = QLineEdit(self)
        self.quick_add_input.setFixedHeight(38)
        self.quick_add_input.setPlaceholderText(
            "\u4e3a\u6240\u9009\u65e5\u671f\u6dfb\u52a0\u8ba1\u5212\uff0c\u56de\u8f66\u5feb\u901f\u65b0\u589e"
        )

        self.quick_add_button = SiSimpleButton(self)
        self.quick_add_button.setFixedHeight(36)
        self.quick_add_button.attachment().setText("\u6dfb\u52a0")
        self.quick_add_button.adjustSize()

        self.quick_add_row = SiDenseHContainer(self)
        self.quick_add_row.setFixedHeight(40)
        self.quick_add_row.setAlignCenter(True)
        self.quick_add_row.addWidget(self.quick_add_input)
        self.quick_add_row.addPlaceholder(8, "right")
        self.quick_add_row.addWidget(self.quick_add_button, "right")

        self.day_tasks_list = QListWidget(self)
        self.day_tasks_list.setFixedHeight(190)
        self.day_tasks_list.setSpacing(6)
        self.day_tasks_list.setAlternatingRowColors(False)

        self.no_task_label = SiLabel(self)
        self.no_task_label.setText("\u5f53\u5929\u6682\u65e0\u8ba1\u5212")

        self.delete_task_button = SiSimpleButton(self)
        self.delete_task_button.setFixedHeight(36)
        self.delete_task_button.attachment().setText("\u5220\u9664\u9009\u4e2d\u8ba1\u5212")
        self.delete_task_button.adjustSize()

        self.action_feedback_label = SiLabel(self)
        self.action_feedback_label.setWordWrap(True)
        self.action_feedback_label.setText("")

        self.selected_date_label.hide()
        self.quick_add_input.hide()
        self.quick_add_button.hide()
        self.quick_add_row.hide()
        self.day_tasks_list.hide()
        self.no_task_label.hide()
        self.delete_task_button.hide()
        self.action_feedback_label.hide()

        self.body().setAdjustWidgetsSize(True)
        self.body().setSpacing(8)
        self.body().addWidget(self.overview_label)
        self.body().addWidget(self.upcoming_tasks_label)
        self.body().addWidget(self.nav_row)
        self.body().addWidget(self.weekday_header)
        self.body().addWidget(self.days_grid_widget)

        self.nav_prev_button.clicked.connect(lambda: self._moveCurrentMonth(-1))
        self.nav_next_button.clicked.connect(lambda: self._moveCurrentMonth(1))
        self.nav_today_button.clicked.connect(self._onTodayButtonClicked)
        self.import_feishu_button.clicked.connect(self._onImportFeishuButtonClicked)

        self._refreshNavigationButtons()
        self.refreshForSelectedDate()

    def setTaskProvider(self, task_provider):
        self._task_provider = task_provider
        self.refreshForSelectedDate()

    def setAllTasksProvider(self, task_provider):
        self._all_tasks_provider = task_provider
        self.refreshForSelectedDate()

    def setTaskMutators(self, task_creator, task_remover):
        self._task_creator = task_creator
        self._task_remover = task_remover

    def setFeishuImportHandler(self, handler):
        if callable(handler):
            self._feishu_import_handler = handler
        else:
            self._feishu_import_handler = lambda: {"ok": False, "message": "未配置导入处理器"}

    def _onImportFeishuButtonClicked(self):
        self.import_feishu_button.setEnabled(False)
        self.import_feishu_button.attachment().setText("导入中...")
        self.import_feishu_button.adjustSize()
        QCoreApplication.processEvents()

        try:
            result = self._feishu_import_handler()
        except Exception as exc:
            result = {
                "ok": False,
                "message": f"导入失败: {exc}",
            }
        finally:
            self.import_feishu_button.setEnabled(True)
            self.import_feishu_button.attachment().setText("\u5bfc\u5165\u98de\u4e66")
            self.import_feishu_button.adjustSize()

        message = result.get("message", "")
        detail = result.get("detail", "")
        if detail:
            message = f"{message}\n{detail}".strip()

        if result.get("ok", False):
            QMessageBox.information(self, "飞书导入", message or "导入完成")
        else:
            QMessageBox.warning(self, "飞书导入", message or "导入失败")

    def _onTodayButtonClicked(self):
        self.selected_date = QDate.currentDate()
        self.current_month_date = QDate(self.selected_date.year(), self.selected_date.month(), 1)
        self.refreshForSelectedDate()

    def _moveCurrentMonth(self, delta_months: int):
        self.current_month_date = self.current_month_date.addMonths(delta_months)
        target_day = min(self.selected_date.day(), self.current_month_date.daysInMonth())
        self.selected_date = QDate(self.current_month_date.year(), self.current_month_date.month(), target_day)
        self.refreshForSelectedDate()

    def _onDayCellClicked(self, cell_index: int):
        if cell_index < 0 or cell_index >= len(self._visible_dates):
            return
        target_date = self._visible_dates[cell_index]
        if not target_date.isValid():
            return

        self.selected_date = target_date
        self.current_month_date = QDate(target_date.year(), target_date.month(), 1)
        self.refreshForSelectedDate()
        self._openEventDialog(target_date.toString("yyyy-MM-dd"))

    def _openEventDialog(self, date_key: str):
        dialog = CalendarEventDialog(
            self,
            date_key=date_key,
            task_provider=self._task_provider,
            task_creator=self._task_creator,
            task_remover=self._task_remover,
        )
        dialog.exec_()
        self.refreshForSelectedDate()

    def _onQuickAddRequested(self):
        task_text = self.quick_add_input.text().strip()
        if task_text == "":
            self._setActionFeedback("\u8bf7\u5148\u8f93\u5165\u8ba1\u5212\u5185\u5bb9", is_error=True)
            return

        if (not self.selected_date.isValid()) or self.selected_date < QDate.currentDate():
            self._setActionFeedback("\u4e0d\u80fd\u6dfb\u52a0\u8fc7\u53bb\u65e5\u671f\u7684\u8ba1\u5212", is_error=True)
            return

        date_key = self.selected_date.toString("yyyy-MM-dd")
        result = self._task_creator(date_key, task_text)
        if result is False:
            self._setActionFeedback("\u6dfb\u52a0\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5", is_error=True)
            return

        self.quick_add_input.clear()
        self._setActionFeedback("\u5df2\u6dfb\u52a0\u5230\u5f53\u65e5\u8ba1\u5212")
        self.refreshForSelectedDate()

    def _onDeleteSelectedTask(self):
        selected_items = self.day_tasks_list.selectedItems()
        if len(selected_items) == 0:
            self._setActionFeedback("\u8bf7\u5148\u9009\u4e2d\u4e00\u6761\u8ba1\u5212", is_error=True)
            return

        selected_item = selected_items[0]
        task_text, occurrence_index = selected_item.data(Qt.UserRole)
        date_key = self.selected_date.toString("yyyy-MM-dd")

        result = self._task_remover(date_key, task_text, occurrence_index)
        if result is False:
            self._setActionFeedback("\u5220\u9664\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5", is_error=True)
            return

        self._setActionFeedback("\u5df2\u5220\u9664\u9009\u4e2d\u8ba1\u5212")
        self.refreshForSelectedDate()

    def _onDayTaskSelectionChanged(self):
        selected_row = self.day_tasks_list.currentRow()
        for row in range(self.day_tasks_list.count()):
            item = self.day_tasks_list.item(row)
            widget = self.day_tasks_list.itemWidget(item)
            if widget is None:
                continue
            if isinstance(widget, CalendarTaskCard):
                widget.setSelectedState(row == selected_row)

    def _setActionFeedback(self, message: str, is_error: bool = False):
        if is_error:
            self.action_feedback_label.setStyleSheet("color: #d9534f")
        else:
            self.action_feedback_label.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_D"]))
        self.action_feedback_label.setText(message)

    def _refreshNavigationButtons(self):
        self.month_label.setText(f"{self.current_month_date.year()}\u5e74 {self.current_month_date.month()}\u6708")
        self.nav_prev_button.setText("")
        self.nav_next_button.setText("")
        if os.path.exists(CALENDAR_LEFT_ARROW_PATH):
            self.nav_prev_button.setIcon(QIcon(CALENDAR_LEFT_ARROW_PATH))
            self.nav_prev_button.setIconSize(QSize(26, 26))
        else:
            self.nav_prev_button.setText("<")

        if os.path.exists(CALENDAR_RIGHT_ARROW_PATH):
            self.nav_next_button.setIcon(QIcon(CALENDAR_RIGHT_ARROW_PATH))
            self.nav_next_button.setIconSize(QSize(26, 26))
        else:
            self.nav_next_button.setText(">")

    def _renderMonthGrid(self, all_tasks):
        tasks_by_date = {}
        for task_date, task_text in all_tasks:
            tasks_by_date.setdefault(task_date, []).append(task_text)

        first_day = QDate(self.current_month_date.year(), self.current_month_date.month(), 1)
        grid_start = first_day.addDays(1 - first_day.dayOfWeek())

        self._visible_dates = []
        for index, day_cell in enumerate(self.day_cells):
            target_date = grid_start.addDays(index)
            self._visible_dates.append(target_date)
            date_key = target_date.toString("yyyy-MM-dd")
            date_tasks = tasks_by_date.get(date_key, [])

            parsed_events = []
            task_tips = []
            for task_text in date_tasks[:4]:
                parsed_task = parse_calendar_event_text(task_text)
                parsed_events.append(parsed_task)
                if parsed_task["structured"]:
                    tip_line = f"[{parsed_task['label']}] {parsed_task['title']}"
                else:
                    tip_line = parsed_task["title"]
                task_tips.append(tip_line)
            for task_text in date_tasks[4:]:
                parsed_events.append(parse_calendar_event_text(task_text))

            if task_tips:
                day_cell.setToolTip("\n".join(task_tips))
            else:
                day_cell.setToolTip("")

            day_cell.setContent(
                date_obj=target_date,
                in_current_month=(target_date.month() == self.current_month_date.month() and target_date.year() == self.current_month_date.year()),
                is_today=(target_date == QDate.currentDate()),
                is_selected=(target_date == self.selected_date),
                is_weekend=(index % 7 in (5, 6)),
                event_infos=parsed_events,
            )

    def refreshForSelectedDate(self):
        all_tasks = self._all_tasks_provider()
        today = QDate.currentDate()
        week_start = today.addDays(1 - today.dayOfWeek())
        week_end = week_start.addDays(6)

        month_count = 0
        week_count = 0
        today_count = 0
        upcoming_lines = []

        for item_date, item_text in all_tasks:
            parsed_date = QDate.fromString(item_date, "yyyy-MM-dd")
            if not parsed_date.isValid():
                continue

            if parsed_date.year() == today.year() and parsed_date.month() == today.month():
                month_count += 1
            if week_start <= parsed_date <= week_end:
                week_count += 1
            if parsed_date == today:
                today_count += 1

            if parsed_date >= self.selected_date and len(upcoming_lines) < 4:
                parsed_text = parse_calendar_event_text(item_text)
                if parsed_text["structured"]:
                    upcoming_lines.append(f"{item_date}  [{parsed_text['label']}] {parsed_text['title']}")
                else:
                    upcoming_lines.append(f"{item_date}  {parsed_text['title']}")

        self.overview_label.setText(
            f"\u672c\u6708\u8ba1\u5212 {month_count} \u9879 | \u672c\u5468 {week_count} \u9879 | \u4eca\u65e5 {today_count} \u9879"
        )
        if upcoming_lines:
            self.upcoming_tasks_label.setPlainText("\u8fd1\u671f\u8ba1\u5212:\n" + "\n".join(f"- {line}" for line in upcoming_lines))
        else:
            self.upcoming_tasks_label.setPlainText("\u8fd1\u671f\u8ba1\u5212: \u6682\u65e0\u5df2\u5b89\u6392\u4efb\u52a1")

        self._refreshNavigationButtons()
        self._renderMonthGrid(all_tasks)

    def adjustSize(self):
        self.resize(self.width(), 1040)

    def reloadStyleSheet(self):
        self.setThemeColor(SiGlobal.siui.colors["PANEL_THEME"])
        super().reloadStyleSheet()

        self.overview_label.setStyleSheet("color: {}; font-size: 20px; font-weight: 700;".format(SiGlobal.siui.colors["TEXT_B"]))
        self.upcoming_tasks_label.setStyleSheet(
            """
            QTextEdit {
                border: 1px solid %s;
                border-radius: 6px;
                background: %s;
                color: %s;
                padding: 6px;
                font-size: 14px;
            }
            """
            % (
                SiGlobal.siui.colors["BORDER_COLOR"],
                SiGlobal.siui.colors["BACKGROUND_DARK_COLOR"],
                SiGlobal.siui.colors["TEXT_D"],
            )
        )
        self.month_label.setStyleSheet(
            "color: #ffffff; font-weight: 800; font-size: 25px; padding: 0 8px;"
        )

        nav_button_style = """
            QToolButton {
                border: none;
                border-radius: 22px;
                background: rgba(255, 255, 255, 0.25);
                color: white;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 0.42);
            }
        """
        self.nav_prev_button.setStyleSheet(nav_button_style)
        self.nav_next_button.setStyleSheet(nav_button_style)
        self.nav_row.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4facfe, stop:1 #00f2fe);"
            "border-radius: 10px; padding-left: 8px; padding-right: 8px;"
        )
        self.nav_today_button.setColor(SiGlobal.siui.colors["SIMPLE_BUTTON_BG"])
        self.import_feishu_button.setColor(SiGlobal.siui.colors["SIMPLE_BUTTON_BG"])

        self.weekday_header.setStyleSheet(
            "background-color: #ffffff; border: 1px solid #d6dbe3; border-bottom: 0px; border-radius: 0px;"
        )
        for index, weekday_label in enumerate(self.weekday_labels):
            text_color = "#6c757d"
            if index in (5, 6):
                text_color = "#d9534f"
            left_width = 1 if index == 0 else 0
            weekday_label.setStyleSheet(
                f"color: {text_color}; font-weight: 700; font-size: 14px; padding-top:2px; padding-bottom:2px;"
                f"border-left:{left_width}px solid #d6dbe3; border-right:1px solid #d6dbe3; border-top:0px; border-bottom:0px;"
                "background:#ffffff;"
            )

        self.days_grid_widget.setStyleSheet("background-color: #ffffff; border: 0px solid transparent; border-radius: 0px;")

        self._refreshNavigationButtons()
        self._renderMonthGrid(self._all_tasks_provider())

    def showEvent(self, a0):
        super().showEvent(a0)
        self.refreshForSelectedDate()
        self.setForceUseAnimations(True)


class CalendarFloatingWindow(QDialog):
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._allow_close = False
        self.setWindowTitle("日历")
        self.setWindowFlags(Qt.Tool | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self.calendar_panel = CalendarPanel(self)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setWidget(self.calendar_panel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)
        layout.addWidget(self.scroll_area)

    def showCentered(self):
        parent_window = self.parent()
        screen = None
        if parent_window is not None:
            screen = QApplication.screenAt(parent_window.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()

        available = screen.availableGeometry()
        width = min(980, max(760, available.width() - 120))
        height = min(1040, max(640, available.height() - 120))

        self.resize(width, height)
        self._syncCalendarWidth(width - 20)
        self.calendar_panel.refreshForSelectedDate()

        x = available.x() + (available.width() - width) // 2
        y = available.y() + (available.height() - height) // 2
        self.move(x, y)
        self.show()
        QCoreApplication.processEvents()
        self._syncCalendarWidth()
        self.calendar_panel.refreshForSelectedDate()
        self.raise_()
        self.activateWindow()

    def _syncCalendarWidth(self, fallback_width=None):
        viewport_width = self.scroll_area.viewport().width()
        if viewport_width <= 100 and fallback_width is not None:
            viewport_width = fallback_width
        content_width = max(620, viewport_width)
        self.calendar_panel.setFixedWidth(content_width)
        self.calendar_panel.adjustSize()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._syncCalendarWidth()

    def closeForApp(self):
        self._allow_close = True
        self.close()

    def closeEvent(self, event):
        if self._allow_close:
            super().closeEvent(event)
            return

        event.ignore()
        self.hide()
        self.closed.emit()


class SettingsPanel(ThemedOptionCardPlane):
    feishuTestConnectionRequested = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setTitle("设置")
        self.setUseSignals(True)

        # 启用深色模式
        self.use_dark_mode = SingleSettingOption(self)
        self.use_dark_mode.setTitle("深色模式", "在深色主题的计算机上提供更佳的视觉效果")

        self.button_use_dark_mode = SiSwitch(self)
        self.button_use_dark_mode.setFixedHeight(32)
        self.button_use_dark_mode.toggled.connect(load_colors)
        self.button_use_dark_mode.toggled.connect(
            lambda b: SiGlobal.todo_list.settings_parser.modify("USE_DARK_MODE", b))
        self.button_use_dark_mode.setChecked(SiGlobal.todo_list.settings_parser.options["USE_DARK_MODE"])

        self.use_dark_mode.addWidget(self.button_use_dark_mode)
        self.use_dark_mode.addPlaceholder(16)

        # 锁定位置
        self.fix_position = SingleSettingOption(self)
        self.fix_position.setTitle("锁定位置", "阻止拖动窗口以保持位置不变")

        self.button_fix_position = SiSwitch(self)
        self.button_fix_position.setFixedHeight(32)
        self.button_fix_position.toggled.connect(lock_position)
        self.button_fix_position.toggled.connect(
            lambda b: SiGlobal.todo_list.settings_parser.modify("FIXED_POSITION", b))
        self.button_fix_position.setChecked(SiGlobal.todo_list.settings_parser.options["FIXED_POSITION"])

        self.fix_position.addWidget(self.button_fix_position)
        self.fix_position.addPlaceholder(16)

        # 飞书连接
        options = SiGlobal.todo_list.settings_parser.options
        self.feishu = SingleSettingOption(self)
        self.feishu.setTitle("飞书连接", "填写飞书参数后可在日历页手动导入计划")

        self.feishu_app_id_edit = QLineEdit(self)
        self.feishu_app_id_edit.setPlaceholderText("FEISHU_APP_ID")
        self.feishu_app_id_edit.setText(str(options.get("FEISHU_APP_ID", "")))
        self.feishu_app_id_edit.editingFinished.connect(self.applyFeishuSettings)

        self.feishu_app_secret_edit = QLineEdit(self)
        self.feishu_app_secret_edit.setPlaceholderText("FEISHU_APP_SECRET")
        self.feishu_app_secret_edit.setEchoMode(QLineEdit.Password)
        self.feishu_app_secret_edit.setText(str(options.get("FEISHU_APP_SECRET", "")))
        self.feishu_app_secret_edit.editingFinished.connect(self.applyFeishuSettings)

        self.feishu_bitable_app_token_edit = QLineEdit(self)
        self.feishu_bitable_app_token_edit.setPlaceholderText("FEISHU_BITABLE_APP_TOKEN")
        self.feishu_bitable_app_token_edit.setText(str(options.get("FEISHU_BITABLE_APP_TOKEN", "")))
        self.feishu_bitable_app_token_edit.editingFinished.connect(self.applyFeishuSettings)

        self.feishu_bitable_table_id_edit = QLineEdit(self)
        self.feishu_bitable_table_id_edit.setPlaceholderText("FEISHU_BITABLE_TABLE_ID")
        self.feishu_bitable_table_id_edit.setText(str(options.get("FEISHU_BITABLE_TABLE_ID", "")))
        self.feishu_bitable_table_id_edit.editingFinished.connect(self.applyFeishuSettings)

        self.feishu_bitable_view_id_edit = QLineEdit(self)
        self.feishu_bitable_view_id_edit.setPlaceholderText("FEISHU_BITABLE_VIEW_ID (可空)")
        self.feishu_bitable_view_id_edit.setText(str(options.get("FEISHU_BITABLE_VIEW_ID", "")))
        self.feishu_bitable_view_id_edit.editingFinished.connect(self.applyFeishuSettings)

        self.feishu_field_title_edit = QLineEdit(self)
        self.feishu_field_title_edit.setPlaceholderText("FEISHU_FIELD_TITLE")
        self.feishu_field_title_edit.setText(str(options.get("FEISHU_FIELD_TITLE", "")))
        self.feishu_field_title_edit.editingFinished.connect(self.applyFeishuSettings)

        self.feishu_field_date_edit = QLineEdit(self)
        self.feishu_field_date_edit.setPlaceholderText("FEISHU_FIELD_DATE")
        self.feishu_field_date_edit.setText(str(options.get("FEISHU_FIELD_DATE", "")))
        self.feishu_field_date_edit.editingFinished.connect(self.applyFeishuSettings)

        self.feishu_field_daily_report_edit = QLineEdit(self)
        self.feishu_field_daily_report_edit.setPlaceholderText("FEISHU_FIELD_DAILY_REPORT")
        self.feishu_field_daily_report_edit.setText(str(options.get("FEISHU_FIELD_DAILY_REPORT", "")))
        self.feishu_field_daily_report_edit.editingFinished.connect(self.applyFeishuSettings)

        self.feishu_bitable_enabled = SiSwitch(self)
        self.feishu_bitable_enabled.setFixedHeight(32)
        self.feishu_bitable_enabled.setChecked(bool(options.get("FEISHU_BITABLE_ENABLED", True)))
        self.feishu_bitable_enabled.toggled.connect(
            lambda state: SiGlobal.todo_list.settings_parser.modify("FEISHU_BITABLE_ENABLED", state)
        )

        self.feishu_report_enabled = SiSwitch(self)
        self.feishu_report_enabled.setFixedHeight(32)
        self.feishu_report_enabled.setChecked(bool(options.get("FEISHU_REPORT_API_ENABLED", True)))
        self.feishu_report_enabled.toggled.connect(
            lambda state: SiGlobal.todo_list.settings_parser.modify("FEISHU_REPORT_API_ENABLED", state)
        )

        self.feishu_source_row = SiDenseHContainer(self)
        self.feishu_source_row.setFixedHeight(32)
        self.feishu_source_row.setAlignCenter(True)
        self.feishu_bitable_label = SiLabel(self)
        self.feishu_bitable_label.setText("启用多维表格")
        self.feishu_source_row.addWidget(self.feishu_bitable_label)
        self.feishu_source_row.addWidget(self.feishu_bitable_enabled)
        self.feishu_source_row.addPlaceholder(10)
        self.feishu_report_label = SiLabel(self)
        self.feishu_report_label.setText("启用汇报API")
        self.feishu_source_row.addWidget(self.feishu_report_label, "right")
        self.feishu_source_row.addWidget(self.feishu_report_enabled, "right")

        self.button_test_feishu = SiSimpleButton(self)
        self.button_test_feishu.setFixedHeight(32)
        self.button_test_feishu.attachment().setText("测试飞书连接")
        self.button_test_feishu.adjustSize()
        self.button_test_feishu.clicked.connect(self._onTestFeishuConnectionClicked)

        self.feishu_feedback_label = SiLabel(self)
        self.feishu_feedback_label.setWordWrap(True)
        self.feishu_feedback_label.setText("")

        self.feishu_line_edits = [
            self.feishu_app_id_edit,
            self.feishu_app_secret_edit,
            self.feishu_bitable_app_token_edit,
            self.feishu_bitable_table_id_edit,
            self.feishu_bitable_view_id_edit,
            self.feishu_field_title_edit,
            self.feishu_field_date_edit,
            self.feishu_field_daily_report_edit,
        ]

        self.feishu.addWidget(self.feishu_app_id_edit)
        self.feishu.addWidget(self.feishu_app_secret_edit)
        self.feishu.addWidget(self.feishu_bitable_app_token_edit)
        self.feishu.addWidget(self.feishu_bitable_table_id_edit)
        self.feishu.addWidget(self.feishu_bitable_view_id_edit)
        self.feishu.addWidget(self.feishu_field_title_edit)
        self.feishu.addWidget(self.feishu_field_date_edit)
        self.feishu.addWidget(self.feishu_field_daily_report_edit)
        self.feishu.addWidget(self.feishu_source_row)
        self.feishu.addWidget(self.button_test_feishu)
        self.feishu.addWidget(self.feishu_feedback_label)
        self.feishu.addPlaceholder(12)

        # 第三方资源
        self.third_party_res = SingleSettingOption(self)
        self.third_party_res.setTitle("第三方资源", "本项目使用了 FlatIcon 提供的图标")

        self.button_to_flaticon = SiSimpleButton(self)
        self.button_to_flaticon.setFixedHeight(32)
        self.button_to_flaticon.attachment().setText("前往 FlatIcon")
        self.button_to_flaticon.clicked.connect(lambda: os.system("start https://flaticon.com/"))
        self.button_to_flaticon.adjustSize()

        self.third_party_res.addWidget(self.button_to_flaticon)
        self.third_party_res.addPlaceholder(16)

        # 许可
        self.license = SingleSettingOption(self)
        self.license.setTitle("开源许可证", "本项目采用 GNU General Public License v3.0")

        self.button_license = SiSimpleButton(self)
        self.button_license.setFixedHeight(32)
        self.button_license.attachment().setText("在 Github 上查看")
        self.button_license.clicked.connect(
            lambda: os.system("start https://github.com/ChinaIceF/My-TODOs/blob/main/LICENSE"))
        self.button_license.adjustSize()

        self.license.addWidget(self.button_license)
        self.license.addPlaceholder(16)

        # 关于
        self.about = SingleSettingOption(self)
        self.about.setTitle("关于此软件", "制作者 霏泠Ice 保留所有权利")

        about_button_set = SiDenseHContainer(self)
        about_button_set.setFixedHeight(32)

        self.button_github = SiSimpleButton(self)
        self.button_github.setFixedHeight(32)
        self.button_github.attachment().setText("Github 主页")
        self.button_github.clicked.connect(lambda: os.system("start https://github.com/ChinaIceF"))
        self.button_github.adjustSize()

        self.button_bilibili = SiSimpleButton(self)
        self.button_bilibili.setFixedHeight(32)
        self.button_bilibili.attachment().setText("哔哩哔哩 主页")
        self.button_bilibili.clicked.connect(lambda: os.system("start https://space.bilibili.com/390832893"))
        self.button_bilibili.adjustSize()

        about_button_set.addWidget(self.button_github)
        about_button_set.addWidget(self.button_bilibili)

        self.about.addWidget(about_button_set)
        self.about.addPlaceholder(16)

        # 赞助
        self.donation = SingleSettingOption(self)
        self.donation.setTitle("赞助作者", "为爱发电，您的支持是我最大的动力")

        self.button_donation = SiSimpleButton(self)
        self.button_donation.setFixedHeight(32)
        self.button_donation.attachment().setText("在 Github 上扫码赞助")
        self.button_donation.clicked.connect(lambda: os.system("start https://github.com/ChinaIceF/My-TODOs?tab=readme-ov-file#%E8%B5%9E%E5%8A%A9"))
        self.button_donation.adjustSize()

        self.donation.addWidget(self.button_donation)
        self.donation.addPlaceholder(16)

        # SiliconUI
        self.silicon_ui = SiDenseVContainer(self)
        self.silicon_ui.setAlignCenter(True)

        self.button_silicon_ui = SiSimpleButton(self)
        self.button_silicon_ui.attachment().setFont(SiGlobal.siui.fonts["S_NORMAL"])
        self.button_silicon_ui.attachment().setText("基于 PyQt-SiliconUI 编写")
        self.button_silicon_ui.adjustSize()
        self.button_silicon_ui.clicked.connect(lambda: os.system("start https://github.com/ChinaIceF/PyQt-SiliconUI"))

        self.silicon_ui.addWidget(self.button_silicon_ui)

        # 添加到body
        self.body().setAdjustWidgetsSize(True)
        self.body().addWidget(self.use_dark_mode)
        self.body().addWidget(self.fix_position)
        self.body().addWidget(self.feishu)
        self.body().addWidget(self.third_party_res)
        self.body().addWidget(self.license)
        self.body().addWidget(self.about)
        self.body().addWidget(self.donation)
        self.body().addWidget(self.silicon_ui)
        self.body().addPlaceholder(16)

    def applyFeishuSettings(self):
        parser = SiGlobal.todo_list.settings_parser
        parser.modify("FEISHU_APP_ID", self.feishu_app_id_edit.text().strip())
        parser.modify("FEISHU_APP_SECRET", self.feishu_app_secret_edit.text().strip())
        parser.modify("FEISHU_BITABLE_APP_TOKEN", self.feishu_bitable_app_token_edit.text().strip())
        parser.modify("FEISHU_BITABLE_TABLE_ID", self.feishu_bitable_table_id_edit.text().strip())
        parser.modify("FEISHU_BITABLE_VIEW_ID", self.feishu_bitable_view_id_edit.text().strip())
        parser.modify("FEISHU_FIELD_TITLE", self.feishu_field_title_edit.text().strip())
        parser.modify("FEISHU_FIELD_DATE", self.feishu_field_date_edit.text().strip())
        parser.modify("FEISHU_FIELD_DAILY_REPORT", self.feishu_field_daily_report_edit.text().strip())
        parser.modify("FEISHU_BITABLE_ENABLED", self.feishu_bitable_enabled.isChecked())
        parser.modify("FEISHU_REPORT_API_ENABLED", self.feishu_report_enabled.isChecked())

    def setFeishuFeedback(self, message: str, is_error: bool = False):
        if is_error:
            self.feishu_feedback_label.setStyleSheet("color: #d9534f")
        else:
            self.feishu_feedback_label.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_D"]))
        self.feishu_feedback_label.setText(message)

    def _onTestFeishuConnectionClicked(self):
        self.applyFeishuSettings()
        self.setFeishuFeedback("正在测试飞书连接...")
        self.feishuTestConnectionRequested.emit()

    def reloadStyleSheet(self):
        self.setThemeColor(SiGlobal.siui.colors["PANEL_THEME"])
        super().reloadStyleSheet()

        input_style = """
            border: 1px solid {};
            background-color: {};
            border-radius: 4px;
            padding-left: 8px; padding-right: 8px;
            color: {}
        """.format(
            SiGlobal.siui.colors["BORDER_COLOR"],
            SiGlobal.siui.colors["BACKGROUND_DARK_COLOR"],
            SiGlobal.siui.colors["TEXT_B"],
        )
        for line_edit in self.feishu_line_edits:
            line_edit.setFixedHeight(34)
            line_edit.setStyleSheet(input_style)

        self.feishu_bitable_label.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_D"]))
        self.feishu_report_label.setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_D"]))
        self.button_test_feishu.setColor(SiGlobal.siui.colors["SIMPLE_BUTTON_BG"])
        self.button_to_flaticon.setColor(SiGlobal.siui.colors["SIMPLE_BUTTON_BG"])
        self.button_license.setColor(SiGlobal.siui.colors["SIMPLE_BUTTON_BG"])
        self.button_github.setColor(SiGlobal.siui.colors["SIMPLE_BUTTON_BG"])
        self.button_bilibili.setColor(SiGlobal.siui.colors["SIMPLE_BUTTON_BG"])
        self.button_donation.setColor(SiGlobal.siui.colors["SIMPLE_BUTTON_BG"])
        self.button_silicon_ui.attachment().setStyleSheet("color: {}".format(SiGlobal.siui.colors["TEXT_E"]))

    def showEvent(self, a0):
        super().showEvent(a0)
        self.setForceUseAnimations(True)


class TODOApplication(QMainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # 窗口周围留白，供阴影使用
        self.padding = 48
        self.anchor = QPoint(self.x(), self.y())
        self.fixed_position = QPoint(SiGlobal.todo_list.settings_parser.options["FIXED_POSITION_X"],
                                     SiGlobal.todo_list.settings_parser.options["FIXED_POSITION_Y"])
        self.compact_window_width = 500
        self.expanded_window_width = 980
        self._panel_states_before_calendar = {
            "todo": True,
            "add": False,
            "settings": False,
        }

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)  # 设置窗口背景透明

        # 初始化全局变量
        SiGlobal.todo_list.todo_list_unfold_state = True
        SiGlobal.todo_list.add_todo_unfold_state = False

        # 初始化工具提示窗口
        SiGlobal.siui.windows["TOOL_TIP"] = ToolTipWindow()
        SiGlobal.siui.windows["TOOL_TIP"].show()
        SiGlobal.siui.windows["TOOL_TIP"].hide_()
        SiGlobal.siui.windows["MAIN_WINDOW"] = self

        # 创建移动动画
        self.move_animation = SiExpAnimation(self)
        self.move_animation.setFactor(1 / 4)
        self.move_animation.setBias(1)
        self.move_animation.setCurrent([self.x(), self.y()])
        self.move_animation.ticked.connect(self._onMoveAnimationTicked)

        # 创建垂直容器
        self.container_v = SiDenseVContainer(self)
        self.container_v.setFixedWidth(self.compact_window_width)
        self.container_v.setSpacing(0)
        self.container_v.setShrinking(True)
        self.container_v.setAlignCenter(True)

        # 构建界面
        # 头
        self.header_panel = AppHeaderPanel(self)
        self.header_panel.setFixedWidth(self.compact_window_width - 2 * self.padding)
        self.header_panel.setFixedHeight(48 + 12)

        # 设置面板
        self.settings_panel = SettingsPanel(self)
        self.settings_panel.setFixedWidth(self.compact_window_width - 2 * self.padding)
        self.settings_panel.adjustSize()

        self.settings_panel_placeholder = SiLabel(self)
        self.settings_panel_placeholder.setFixedHeight(12)
        self._onSettingsButtonToggled(False)

        # 添加新待办面板
        self.add_todo_panel = AddNewTODOPanel(self)
        self.add_todo_panel.setFixedWidth(self.compact_window_width - 2 * self.padding)
        self.add_todo_panel.adjustSize()

        self.add_todo_panel_placeholder = SiLabel(self)
        self.add_todo_panel_placeholder.setFixedHeight(12)
        self._onAddTODOButtonToggled(False)

        self.calendar_window = CalendarFloatingWindow(self)
        self.calendar_panel = self.calendar_window.calendar_panel
        self.calendar_window.closed.connect(self._onCalendarWindowClosed)

        # 待办列表面板
        self.todo_list_panel = TODOListPanel(self)
        self.todo_list_panel.setFixedWidth(self.compact_window_width - 2 * self.padding)

        self.todo_list_panel_placeholder = SiLabel(self)
        self.todo_list_panel_placeholder.setFixedHeight(12)
        self._onShowTODOButtonToggled(True)

        # <- 添加到垂直容器
        self.container_v.addWidget(self.header_panel)
        self.container_v.addWidget(self.settings_panel)
        self.container_v.addWidget(self.settings_panel_placeholder)
        self.container_v.addWidget(self.add_todo_panel)
        self.container_v.addWidget(self.add_todo_panel_placeholder)
        self.container_v.addWidget(self.todo_list_panel)
        self.container_v.addWidget(self.todo_list_panel_placeholder)

        # 绑定界面信号
        self.header_panel.unfold_button.toggled.connect(self._onShowTODOButtonToggled)
        self.header_panel.add_todo_button.toggled.connect(self._onAddTODOButtonToggled)
        self.header_panel.settings_button.toggled.connect(self._onSettingsButtonToggled)
        self.header_panel.calendar_button.toggled.connect(self._onCalendarButtonToggled)

        self.settings_panel.resized.connect(self._onTODOWindowResized)
        self.add_todo_panel.resized.connect(self._onTODOWindowResized)
        self.todo_list_panel.resized.connect(self._onTODOWindowResized)
        self.settings_panel.feishuTestConnectionRequested.connect(self._onFeishuTestConnectionRequested)

        self.add_todo_panel.confirm_button.clicked.connect(self._onAddTODOConfirmButtonClicked)
        self.add_todo_panel.cancel_button.clicked.connect(self._onAddTODOCancelButtonClicked)

        self.todo_list_panel.todoAmountChanged.connect(self._onTODOAmountChanged)

        self.calendar_panel.setTaskProvider(self.todo_list_panel.getDailyTasks)
        self.calendar_panel.setAllTasksProvider(self.todo_list_panel.getScheduledTasks)
        self.calendar_panel.setTaskMutators(self.todo_list_panel.addTaskForDate, self.todo_list_panel.removeTask)
        self.calendar_panel.setFeishuImportHandler(self._onFeishuImportRequested)
        SiGlobal.todo_list.refresh_calendar = self.calendar_panel.refreshForSelectedDate

        self.resize(self.compact_window_width, 800)
        self.move(self.fixed_position.x(), self.fixed_position.y())
        SiGlobal.siui.reloadAllWindowsStyleSheet()

        # 读取 todos.ini 添加到待办
        for todo in SiGlobal.todo_list.todos_parser.todos:
            todo_text, todo_date = parse_task_entry(todo)
            self.todo_list_panel.addTODO(todo_text, todo_date)

        self.calendar_panel.refreshForSelectedDate()


    def adjustSize(self):
        h = (self.header_panel.height() + 12 +
             self.settings_panel.height() + 12 +
             self.add_todo_panel.height() + 12 +
             self.todo_list_panel.height() +
             2 * self.padding)
        self.resize(self.width(), h)
        self.container_v.adjustSize()

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        self.container_v.move(0, self.padding)

    def showEvent(self, a0):
        super().showEvent(a0)

    def _setWindowLayoutWidth(self, window_width):
        content_width = window_width - 2 * self.padding
        self.container_v.setFixedWidth(window_width)

        for panel_name in ("header_panel", "settings_panel", "add_todo_panel", "todo_list_panel"):
            panel = getattr(self, panel_name, None)
            if panel is not None:
                panel.setFixedWidth(content_width)

        self.resize(window_width, self.height())

    def _onTODOWindowResized(self, size):
        w, h = size
        self.adjustSize()

    def _onShowTODOButtonToggled(self, state):
        if state is True:
            self.todo_list_panel_placeholder.setFixedHeight(12)
            self.todo_list_panel.adjustSize()
        else:
            self.todo_list_panel_placeholder.setFixedHeight(0)
            self.todo_list_panel.resize(self.todo_list_panel.width(), 0)

    def _onAddTODOButtonToggled(self, state):
        if state is True:
            self.add_todo_panel.prepareForOpen()
            self.add_todo_panel_placeholder.setFixedHeight(12)
            self.add_todo_panel.adjustSize()
        else:
            self.add_todo_panel_placeholder.setFixedHeight(0)
            self.add_todo_panel.resize(self.add_todo_panel.width(), 0)

    def _onSettingsButtonToggled(self, state):
        if state is True:
            self.settings_panel_placeholder.setFixedHeight(12)
            self.settings_panel.adjustSize()
        else:
            self.settings_panel_placeholder.setFixedHeight(0)
            self.settings_panel.resize(self.settings_panel.width(), 0)

    def _onCalendarButtonToggled(self, state):
        if state is True:
            self.calendar_window.showCentered()
        else:
            self.calendar_window.hide()
        if hasattr(self, "todo_list_panel"):
            self.adjustSize()

    def _onCalendarWindowClosed(self):
        if self.header_panel.calendar_button.isChecked():
            self.header_panel.calendar_button.blockSignals(True)
            self.header_panel.calendar_button.setChecked(False)
            self.header_panel.calendar_button.blockSignals(False)

    def _onTODOAmountChanged(self, amount):
        if amount == 0:
            self.header_panel.unfold_button.attachment().setText("没有待办")
        else:
            self.header_panel.unfold_button.attachment().setText(f"{amount}个待办事项")
        self.header_panel.unfold_button.adjustSize()

    def _onAddTODOConfirmButtonClicked(self):
        text = self.add_todo_panel.text_edit.toPlainText()

        while text[-1:] == "\n":
            text = text[:-1]

        if text.strip() == "":
            self.add_todo_panel.setFeedback("\u8bf7\u5148\u8f93\u5165\u5f85\u529e\u5185\u5bb9", is_error=True)
            return

        task_date = self.add_todo_panel.getTaskDate()
        if task_date is not None:
            parsed_task_date = QDate.fromString(task_date, "yyyy-MM-dd")
            if (not parsed_task_date.isValid()) or parsed_task_date < QDate.currentDate():
                self.add_todo_panel.setFeedback("\u4e0d\u80fd\u6dfb\u52a0\u8fc7\u53bb\u65e5\u671f\u7684\u8ba1\u5212", is_error=True)
                return
            if self.todo_list_panel.addTaskForDate(task_date, text) is False:
                self.add_todo_panel.setFeedback("\u6dfb\u52a0\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5", is_error=True)
                return
        else:
            self.todo_list_panel.addTODO(text, task_date)

        self.add_todo_panel.setFeedback("\u6dfb\u52a0\u6210\u529f")
        self.add_todo_panel.resetForm()
        self.header_panel.add_todo_button.setChecked(False)

    def _onAddTODOCancelButtonClicked(self):
        self.add_todo_panel.resetForm()
        self.header_panel.add_todo_button.setChecked(False)

    @staticmethod
    def _coerce_bool(value, default=False):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return True
            if lowered in ("0", "false", "no", "off"):
                return False
            return default
        if value is None:
            return default
        return bool(value)

    def _buildFeishuConfig(self):
        self.settings_panel.applyFeishuSettings()
        options = SiGlobal.todo_list.settings_parser.options
        return FeishuConfig(
            app_id=str(options.get("FEISHU_APP_ID", "")).strip(),
            app_secret=str(options.get("FEISHU_APP_SECRET", "")).strip(),
            bitable_app_token=str(options.get("FEISHU_BITABLE_APP_TOKEN", "")).strip(),
            bitable_table_id=str(options.get("FEISHU_BITABLE_TABLE_ID", "")).strip(),
            bitable_view_id=str(options.get("FEISHU_BITABLE_VIEW_ID", "")).strip(),
            field_title=str(options.get("FEISHU_FIELD_TITLE", "")).strip(),
            field_date=str(options.get("FEISHU_FIELD_DATE", "")).strip(),
            field_daily_report=str(options.get("FEISHU_FIELD_DAILY_REPORT", "")).strip(),
            bitable_enabled=self._coerce_bool(options.get("FEISHU_BITABLE_ENABLED", True), True),
            report_api_enabled=self._coerce_bool(options.get("FEISHU_REPORT_API_ENABLED", True), True),
            timeout_seconds=10,
        )

    def _buildExistingTaskKeys(self):
        keys = set()
        for task_date, task_text in self.todo_list_panel.getScheduledTasks():
            parsed = parse_calendar_event_text(task_text)
            normalized_title = (parsed.get("title") or "").strip()
            if task_date and normalized_title:
                keys.add(f"{task_date}|{normalized_title}")
        return keys

    def _getImportDateWindow(self):
        q_start = QDate.currentDate()
        q_end = q_start.addDays(IMPORT_RANGE_DAYS)
        start_date = date(q_start.year(), q_start.month(), q_start.day())
        end_date = date(q_end.year(), q_end.month(), q_end.day())
        return start_date, end_date

    def _onFeishuImportRequested(self):
        try:
            config = self._buildFeishuConfig()
            service = FeishuImportService(config)
            existing_keys = self._buildExistingTaskKeys()
            start_date, end_date = self._getImportDateWindow()
            result = service.import_tasks(
                existing_keys=existing_keys,
                start_date=start_date,
                end_date=end_date,
            )
        except FeishuImportError as exc:
            message = str(exc)
            self.settings_panel.setFeishuFeedback(message, is_error=True)
            return {"ok": False, "message": message}
        except Exception as exc:
            message = f"导入失败: {exc}"
            self.settings_panel.setFeishuFeedback(message, is_error=True)
            return {"ok": False, "message": message}

        add_failed = 0
        add_success = 0
        for task in result.get("tasks", []):
            task_title = (task.get("title") or "").strip()
            task_description = (task.get("description") or "").strip()
            task_date = (task.get("date") or "").strip()
            encoded_text = encode_calendar_event(title=task_title, event_type="normal", description=task_description)
            if self.todo_list_panel.addTaskForDate(task_date, encoded_text):
                add_success += 1
            else:
                add_failed += 1

        self.calendar_panel.refreshForSelectedDate()
        failed_total = int(result.get("failed_sources", 0)) + add_failed
        summary = (
            f"新增{add_success}条 / 跳过重复{int(result.get('skipped_duplicate', 0))}条 / "
            f"跳过历史{int(result.get('skipped_history', 0))}条 / 失败{failed_total}条"
        )

        details = []
        source_errors = result.get("source_errors", {})
        if "bitable" in source_errors:
            details.append(f"多维表格: {source_errors['bitable']}")
        if "report" in source_errors:
            details.append(f"汇报API: {source_errors['report']}")
        out_of_range_count = int(result.get("skipped_out_of_range", 0))
        if out_of_range_count > 0:
            details.append(f"跳过范围外任务: {out_of_range_count}条")
        invalid_count = int(result.get("skipped_invalid", 0))
        if invalid_count > 0:
            details.append(f"跳过无效任务: {invalid_count}条")

        is_ok = failed_total == 0
        self.settings_panel.setFeishuFeedback(summary, is_error=not is_ok)
        return {
            "ok": is_ok,
            "message": summary,
            "detail": "\n".join(details).strip(),
        }

    def _onFeishuTestConnectionRequested(self):
        try:
            config = self._buildFeishuConfig()
            service = FeishuImportService(config)
            start_date, end_date = self._getImportDateWindow()
            result = service.test_connection(start_date=start_date, end_date=end_date)
        except FeishuImportError as exc:
            message = str(exc)
            self.settings_panel.setFeishuFeedback(message, is_error=True)
            QMessageBox.warning(self, "飞书连接测试", message)
            return
        except Exception as exc:
            message = f"连接测试失败: {exc}"
            self.settings_panel.setFeishuFeedback(message, is_error=True)
            QMessageBox.warning(self, "飞书连接测试", message)
            return

        if result.get("ok", False):
            message = "连接成功：鉴权、多维表格、汇报API均可用"
            self.settings_panel.setFeishuFeedback(message)
            QMessageBox.information(self, "飞书连接测试", message)
            return

        details = []
        for source_name, source_error in result.get("errors", {}).items():
            if source_name == "bitable":
                details.append(f"多维表格: {source_error}")
            elif source_name == "report":
                details.append(f"汇报API: {source_error}")
            else:
                details.append(f"{source_name}: {source_error}")
        message = "连接测试完成，但存在错误"
        detail_text = "\n".join(details).strip()
        self.settings_panel.setFeishuFeedback(message + (f"；{detail_text}" if detail_text else ""), is_error=True)
        QMessageBox.warning(self, "飞书连接测试", (message + "\n" + detail_text).strip())

    def moveTo(self, x, y):
        self.move_animation.setTarget([x, y])
        self.move_animation.try_to_start()

    def moveEvent(self, a0):
        super().moveEvent(a0)
        x, y = a0.pos().x(), a0.pos().y()
        self.move_animation.setCurrent([x, y])

    def _onMoveAnimationTicked(self, pos):
        self.move(int(pos[0]), int(pos[1]))
        if SiGlobal.todo_list.position_locked is False:
            self.fixed_position = self.pos()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            self.anchor = event.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if not (event.buttons() & Qt.LeftButton):
            return

        new_pos = event.pos() - self.anchor + self.frameGeometry().topLeft()
        x, y = new_pos.x(), new_pos.y()

        self.moveTo(x, y)

    def mouseReleaseEvent(self, a0):
        if SiGlobal.todo_list.position_locked is True:
            self.moveTo(self.fixed_position.x(), self.fixed_position.y())

    def closeEvent(self, a0):
        super().closeEvent(a0)

        # 获取当前待办，并写入 todos.ini
        SiGlobal.todo_list.todos_parser.todos = self.todo_list_panel.getRawTodos()
        SiGlobal.todo_list.todos_parser.write()

        # 写入设置到 options.ini
        self.settings_panel.applyFeishuSettings()
        SiGlobal.todo_list.settings_parser.modify("FIXED_POSITION_X", self.fixed_position.x())
        SiGlobal.todo_list.settings_parser.modify("FIXED_POSITION_Y", self.fixed_position.y())
        SiGlobal.todo_list.settings_parser.write()

        if hasattr(self, "calendar_window"):
            self.calendar_window.closeForApp()

        SiGlobal.siui.windows["TOOL_TIP"].close()
        QCoreApplication.quit()

