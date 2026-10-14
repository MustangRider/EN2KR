"""First-run guide. Replaces v1's VB-Cable wizard: nothing to set up outside the app."""
from PySide6 import QtCore, QtWidgets

from .. import CONTACT

STEPS = [
    ("번역 API 등록", "DeepL·Azure·Google 등 무료 번역 API 키를 하나 이상 등록합니다. "
                     "여러 개를 등록하면 한도가 끝났을 때 자동으로 다음 API로 넘어갑니다.", "api"),
    ("게임 실행 후 프로그램 선택", "게임을 켠 뒤 메인 창 위쪽 '번역할 프로그램'에서 게임을 고릅니다. "
                              "ARC Raiders(PioneerGame.exe)는 기본으로 선택되어 있습니다. "
                              "VB-Cable이나 Windows 사운드 설정은 필요 없습니다.", None),
    ("인식 엔진 확인", "영어만 들을 거라면 '영어 전용'이 가장 정확합니다. 다국어나 한국어 건너뛰기가 필요하면 "
                    "다국어 엔진을 고르세요. VRAM 사용량을 보고 크기를 정할 수 있습니다.", "engine"),
    ("오버레이 위치 조정", "F12로 편집 모드에 들어가 자막 창을 끌어 옮기고, 오른쪽 아래 모서리로 크기를 조절합니다. "
                       "다시 F12를 누르면 클릭이 게임으로 통과됩니다.", None),
    ("게임 화면 모드", "'전체 화면(독점)' 모드에서는 오버레이가 보이지 않습니다. "
                    "게임 그래픽 설정에서 '테두리 없는 창 모드(Borderless)'를 사용하세요.", None),
]


class OnboardingDialog(QtWidgets.QDialog):
    open_api = QtCore.Signal()
    open_engine = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("EN2KR4GAMING 시작 안내")
        self.setMinimumWidth(620)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("<h2>실시간 게임 음성 번역 오버레이</h2>")
        layout.addWidget(title)
        for i, (head, body, action) in enumerate(STEPS, 1):
            box = QtWidgets.QGroupBox(f"{i}. {head}")
            bl = QtWidgets.QHBoxLayout(box)
            lab = QtWidgets.QLabel(body)
            lab.setWordWrap(True)
            bl.addWidget(lab, 1)
            if action:
                btn = QtWidgets.QPushButton("지금 설정" if action == "api" else "열기")
                btn.clicked.connect(self.open_api.emit if action == "api" else self.open_engine.emit)
                bl.addWidget(btn)
            layout.addWidget(box)
        contact = QtWidgets.QLabel(f"문의: {CONTACT}")
        contact.setObjectName("muted")
        layout.addWidget(contact)
        ok = QtWidgets.QPushButton("시작하기")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        layout.addWidget(ok)
