"""First-run guide."""
from PySide6 import QtCore, QtWidgets

from .. import CONTACT


def _steps(edit_key):
    return [
        ("번역 API 등록",
         "번역 API 키를 하나 이상 등록하세요. 여러 개를 등록하면 한도가 끝나거나 오류가 날 때 다음 API로 자동 전환됩니다.",
         "api"),
        ("번역할 소리 선택",
         "게임을 켠 뒤 메인 창의 '번역할 프로그램'에서 게임을 고르면 그 프로그램의 소리만 번역합니다.<br>"
         "게임 효과음 없이 음성 채팅만 번역하려면 목록 맨 위의 <b>음성 채팅만 번역 (VB-CABLE)</b>을 고르세요.",
         None),
        ("인식 엔진",
         "영어만 듣는다면 '영어 전용'이 가장 정확합니다. 다른 언어가 섞이면 다국어 엔진을 고르세요. "
         "모델마다 VRAM 사용량이 표시됩니다.",
         "engine"),
        ("자막 위치",
         f"<b>{edit_key.upper()}</b>로 편집 모드에 들어가 자막을 끌어 옮기고, 오른쪽 아래 모서리로 크기를 조절하세요. "
         f"다시 {edit_key.upper()}를 누르면 마우스 클릭이 게임으로 통과됩니다.",
         None),
        ("게임 화면 모드",
         "독점 전체 화면에서는 자막이 보이지 않습니다. 게임을 '테두리 없는 창 모드'로 실행하세요.",
         None),
    ]


class OnboardingDialog(QtWidgets.QDialog):
    open_api = QtCore.Signal()
    open_engine = QtCore.Signal()

    def __init__(self, edit_key="f12", parent=None):
        super().__init__(parent)
        self.setWindowTitle("시작 안내")
        self.setFixedWidth(640)  # word-wrapped rich text needs a known width to compute its height
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(QtWidgets.QLabel("<h2>실시간 게임 음성 번역 오버레이</h2>"))
        for i, (head, body, action) in enumerate(_steps(edit_key), 1):
            box = QtWidgets.QGroupBox(f"{i}. {head}")
            bl = QtWidgets.QHBoxLayout(box)
            lab = QtWidgets.QLabel(body)
            lab.setWordWrap(True)
            lab.setTextFormat(QtCore.Qt.TextFormat.RichText)
            bl.addWidget(lab, 1)
            if action:
                btn = QtWidgets.QPushButton("설정하기" if action == "api" else "열기")
                btn.clicked.connect(self.open_api.emit if action == "api" else self.open_engine.emit)
                bl.addWidget(btn)
            layout.addWidget(box)
        footer = QtWidgets.QLabel(f"이 안내는 메인 창의 '사용 안내' 버튼으로 다시 볼 수 있습니다.  ·  문의: {CONTACT}")
        footer.setObjectName("muted")
        layout.addWidget(footer)
        ok = QtWidgets.QPushButton("시작하기")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        layout.addWidget(ok)
        layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetFixedSize)
