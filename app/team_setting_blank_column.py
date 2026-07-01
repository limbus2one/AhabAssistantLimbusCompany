from PySide6.QtWidgets import QFrame, QVBoxLayout


class TeamSettingBlankColumn(QFrame):
    """队伍设置页左侧预留空白列。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TeamSettingBlankColumn")
        self.setFixedWidth(120)

        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(0)
