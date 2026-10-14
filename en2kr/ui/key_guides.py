"""Step-by-step key guides for services that have no Korean sign-up guide of their own."""
import webbrowser

from PySide6 import QtCore, QtWidgets

GROQ_KEYS_URL = "https://console.groq.com/keys"
GROQ_LIMITS_URL = "https://console.groq.com/settings/limits"

GROQ_GUIDE = (
    "<b>Groq API 키 발급 (무료)</b><br><br>"
    f"1. 아래 'Groq 콘솔 열기'를 누릅니다 (<a href='{GROQ_KEYS_URL}'>console.groq.com/keys</a>).<br>"
    "2. Google 계정 등으로 가입·로그인합니다. 무료 플랜은 결제 카드를 등록하지 않아도 됩니다.<br>"
    "3. API Keys 화면에서 <b>Create API Key</b>를 누르고, 알아보기 쉬운 이름(예: EN2KR)을 넣어 만듭니다.<br>"
    "4. <b>gsk_</b>로 시작하는 키가 나오면 바로 복사합니다. "
    "<b>키는 이때 한 번만 보입니다.</b> 창을 닫아 버렸다면 키를 새로 만드세요.<br>"
    "5. 이 프로그램의 키 입력 칸에 붙여 넣고 '연결 테스트'를 누릅니다.<br><br>"
    "• 같은 키를 음성 인식(인식 엔진 → 클라우드 · Groq)과 번역(번역 API → OpenAI 호환 → Groq)에 함께 쓸 수 있습니다.<br>"
    "• 키는 이 PC의 Windows 계정으로 암호화해서 저장됩니다. 채팅·게시판 같은 곳에는 붙여 넣지 마세요. "
    "남에게 알려졌다면 같은 화면에서 삭제하고 새로 만드세요.<br>"
    f"• 남은 한도: <a href='{GROQ_LIMITS_URL}'>console.groq.com/settings/limits</a>"
)


class KeyGuideDialog(QtWidgets.QDialog):
    def __init__(self, title, html_text, url, open_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QtWidgets.QVBoxLayout(self)
        lab = QtWidgets.QLabel(html_text)
        lab.setTextFormat(QtCore.Qt.TextFormat.RichText)
        lab.setWordWrap(True)
        lab.setOpenExternalLinks(True)
        lab.setMinimumWidth(460)
        lay.addWidget(lab)
        row = QtWidgets.QHBoxLayout()
        open_btn = QtWidgets.QPushButton(open_text)
        open_btn.setObjectName("primary")
        open_btn.clicked.connect(lambda: webbrowser.open(url))
        close = QtWidgets.QPushButton("닫기")
        close.clicked.connect(self.accept)
        row.addStretch()
        row.addWidget(open_btn)
        row.addWidget(close)
        lay.addLayout(row)
        lay.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetFixedSize)


def show_groq(parent=None):
    KeyGuideDialog("Groq API 키 발급 안내", GROQ_GUIDE, GROQ_KEYS_URL, "Groq 콘솔 열기", parent).exec()
