from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox

from app import mediator
from app.base_tools import BaseCheckBox
from tasks.mirror.theme_pack_images import catalog


class ObserveThemePackModule(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ObserveThemePackModule")
        layout = QVBoxLayout(self)
        layout.addWidget(BaseCheckBox("observe_theme_pack", None, self.tr("启用卡包观测")))
        layout.addWidget(BodyLabel(self.tr("仅支持英文游戏的 Normal / Hard，第1–5层。")))
        self.boxes = []
        self.options = []
        for floor in range(1, 6):
            row = QHBoxLayout()
            row.addWidget(BodyLabel(self.tr("第{}层").format(floor)))
            box = ComboBox(self)
            entries = [("", self.tr("不观测，按权重选择"))]
            for pack_id, pack in catalog()["packs"].items():
                if any(floor in floors for floors in pack["floors"].values()):
                    entries.append((pack_id, f"{pack['zh']} / {pack['en']}"))
            entries[1:] = sorted(entries[1:], key=lambda entry: entry[1])
            box.addItems([label for _, label in entries])
            self.options.append([pack_id for pack_id, _ in entries])
            self.boxes.append(box)
            box.currentIndexChanged.connect(self._changed)
            row.addWidget(box, 1)
            layout.addLayout(row)

    def _changed(self, _index):
        mediator.team_setting.emit(
            {
                "observe_theme_pack_selected": [
                    options[box.currentIndex()] for box, options in zip(self.boxes, self.options)
                ]
            }
        )

    def load_selected(self, values):
        for box, options, value in zip(self.boxes, self.options, values):
            box.blockSignals(True)
            box.setCurrentIndex(options.index(value) if value in options else 0)
            box.blockSignals(False)
