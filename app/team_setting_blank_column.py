from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import ScrollArea, SmoothMode


class TeamSettingTeamLabel(QLabel):
    clicked = Signal(int)
    NORMAL_STYLE = """
        QLabel {
            background-color: rgba(128, 128, 128, 0.35);
            border: 1px solid transparent;
            border-radius: 4px;
            font-size: 15px;
            padding: 3px 3px;
        }
        QLabel:hover {
            border: 1px solid rgba(255, 128, 128, 0.85);
        }
    """
    SELECTED_STYLE = """
        QLabel {
            background-color: rgba(255, 64, 64, 0.9);
            border: 1px solid rgba(255, 128, 128, 0.95);
            border-radius: 4px;
            color: white;
            font-size: 15px;
            font-weight: 600;
            padding: 3px 3px;
        }
        QLabel:hover {
            border: 1px solid rgba(255, 180, 180, 1);
        }
    """

    def __init__(self, team_number: int, parent=None):
        super().__init__(f"team{team_number}", parent)
        self.team_number = team_number
        self.setFixedWidth(100)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(self.NORMAL_STYLE)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.team_number)
        super().mouseReleaseEvent(event)

    def set_selected(self, selected: bool):
        self.setStyleSheet(self.SELECTED_STYLE if selected else self.NORMAL_STYLE)


class TeamSettingBlankColumn(QFrame):
    """队伍设置页左侧编队列表栏。"""

    team_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TeamSettingBlankColumn")
        self.setFixedWidth(120)
        self.team_labels: dict[int, TeamSettingTeamLabel] = {}

        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(0)

        self.title_label = QLabel("编队", self)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout_.addWidget(self.title_label)
        self.layout_.addSpacing(10)

        self.scroll_area = ScrollArea(self)
        self.scroll_area.setSmoothMode(SmoothMode.LINEAR, Qt.Orientation.Vertical)
        self.scroll_area.scrollDelagate.verticalSmoothScroll.duration = 100
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.enableTransparentBackground()

        self.scroll_widget = QWidget(self)
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setContentsMargins(10, 0, 10, 0)
        self.scroll_layout.setSpacing(10)

        for team_number in range(1, 21):
            team_label = TeamSettingTeamLabel(team_number, self.scroll_widget)
            team_label.clicked.connect(self.team_selected.emit)
            self.team_labels[team_number] = team_label
            self.scroll_layout.addWidget(team_label)

        self.scroll_layout.addStretch()
        self.scroll_area.setWidget(self.scroll_widget)
        self.layout_.addWidget(self.scroll_area, 1)

    def set_current_team(self, team_number: int):
        for number, label in self.team_labels.items():
            label.set_selected(number == team_number)
