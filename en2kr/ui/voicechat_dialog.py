"""Setup for "ARC Raiders voice chat only" (VB-CABLE)."""
import subprocess
import webbrowser

from PySide6 import QtCore, QtWidgets

from ..audio import vbcable
from ..audio.endpoints import RENDER, list_endpoints
from ..audio.voicechat import is_cable_name
from .bridge import Worker

GAME_STEPS = (
    "ARC Raiders → 설정 → 오디오 → <b>음성 채팅</b>에서<br>"
    f"<b>음성 채팅 출력 장치</b>를 <b>{vbcable.INPUT_NAME}</b>로 바꾸세요.<br>"
    "게임 효과음·음악은 지금처럼 헤드셋으로 나오고, 보이스챗만 이 프로그램으로 들어옵니다."
)


class VoiceChatDialog(QtWidgets.QDialog):
    install_progress = QtCore.Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ARC Raiders 보이스챗만 번역 (VB-CABLE)")
        self.setMinimumWidth(640)
        self.settings = settings
        voice = settings.get("audio.voice", {})
        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel("게임 소리(총소리·기계음 등)는 빼고 <b>음성 채팅만</b> 번역합니다. "
                                 "게임의 보이스챗 출력을 가상 장치(VB-CABLE)로 보내고, 프로그램이 그 소리를 받아 "
                                 "번역한 뒤 헤드셋으로 다시 들려줍니다.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # 1. VB-CABLE
        g1 = QtWidgets.QGroupBox("1. VB-CABLE (가상 오디오 장치)")
        l1 = QtWidgets.QVBoxLayout(g1)
        row = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel()
        row.addWidget(self.status, 1)
        self.install_btn = QtWidgets.QPushButton("VB-CABLE 설치")
        self.install_btn.clicked.connect(self._install)
        row.addWidget(self.install_btn)
        site = QtWidgets.QPushButton("VB-Audio 사이트")
        site.clicked.connect(lambda: webbrowser.open(vbcable.HOME_URL))
        row.addWidget(site)
        l1.addLayout(row)
        note = QtWidgets.QLabel(vbcable.ATTRIBUTION + "<br>설치 버튼은 공식 사이트에서 정품 패키지를 받아 "
                                "VB-Audio의 설치 프로그램을 실행합니다(관리자 권한 필요, 설치 후 재부팅 권장).")
        note.setObjectName("muted")
        note.setWordWrap(True)
        l1.addWidget(note)
        self.warn = QtWidgets.QLabel()
        self.warn.setObjectName("warn")
        self.warn.setWordWrap(True)
        self.warn.hide()
        l1.addWidget(self.warn)
        layout.addWidget(g1)

        # 2. game setting
        g2 = QtWidgets.QGroupBox("2. 게임 설정 (한 번만)")
        l2 = QtWidgets.QVBoxLayout(g2)
        steps = QtWidgets.QLabel(GAME_STEPS)
        steps.setWordWrap(True)
        l2.addWidget(steps)
        caution = QtWidgets.QLabel("이 설정을 켠 채로 프로그램을 끄면 보이스챗이 들리지 않습니다. "
                                   "프로그램을 쓰지 않을 때는 게임 설정을 '시스템 기본값'으로 되돌리세요.")
        caution.setObjectName("muted")
        caution.setWordWrap(True)
        l2.addWidget(caution)
        layout.addWidget(g2)

        # 3. monitoring
        g3 = QtWidgets.QGroupBox("3. 보이스챗 듣기")
        f3 = QtWidgets.QFormLayout(g3)
        self.monitor = QtWidgets.QCheckBox("프로그램이 보이스챗을 헤드셋으로 들려주기 (끄면 보이스챗이 안 들립니다)")
        self.monitor.setChecked(voice.get("monitor", True))
        f3.addRow(self.monitor)
        self.device = QtWidgets.QComboBox()
        self.device.addItem("Windows 기본 출력 장치", "")
        for e in list_endpoints(RENDER):
            if not is_cable_name(e.name):
                self.device.addItem(e.name + ("  (현재 기본)" if e.is_default else ""), e.name)
        idx = self.device.findData(voice.get("monitor_device", ""))
        self.device.setCurrentIndex(max(0, idx))
        f3.addRow("들려줄 장치:", self.device)
        vol_row = QtWidgets.QHBoxLayout()
        self.volume = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.volume.setRange(0, 200)
        self.volume.setValue(int(voice.get("monitor_volume", 100)))
        self.vol_label = QtWidgets.QLabel()
        self.volume.valueChanged.connect(lambda v: self.vol_label.setText(f"{v}%"))
        self.vol_label.setText(f"{self.volume.value()}%")
        vol_row.addWidget(self.volume, 1)
        vol_row.addWidget(self.vol_label)
        f3.addRow("볼륨:", vol_row)
        self.monitor.toggled.connect(self.device.setEnabled)
        self.monitor.toggled.connect(self.volume.setEnabled)
        self.device.setEnabled(self.monitor.isChecked())
        self.volume.setEnabled(self.monitor.isChecked())
        layout.addWidget(g3)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                          | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("이 방식으로 번역")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self.install_progress.connect(self.status.setText)
        self._refresh_status()

    def _refresh_status(self):
        if vbcable.is_installed():
            self.status.setText("<span style='color:#3fb950'>● 설치됨</span>")
            self.install_btn.setText("다시 설치")
        else:
            self.status.setText("<span style='color:#f85149'>● 설치되어 있지 않음</span>")
            self.install_btn.setText("VB-CABLE 설치")
        if vbcable.default_output_is_cable():
            self.warn.setText("Windows 기본 출력 장치가 VB-CABLE로 바뀌어 있습니다. 이대로면 게임 소리가 안 들립니다. "
                              "Windows 사운드 설정에서 헤드셋을 기본 장치로 되돌리세요.")
            self.warn.show()
            if not hasattr(self, "_sound_btn"):
                self._sound_btn = QtWidgets.QPushButton("Windows 사운드 설정 열기")
                self._sound_btn.clicked.connect(lambda: subprocess.Popen(["control", "mmsys.cpl", "sounds"]))
                self.warn.parentWidget().layout().addWidget(self._sound_btn)
        else:
            self.warn.hide()

    def _install(self):
        self.install_btn.setEnabled(False)
        self.status.setText("공식 패키지 다운로드 중...")
        worker = Worker(self)

        def job():
            path = vbcable.download(lambda d, t: self.install_progress.emit(
                f"다운로드 중... {d // 1024:,} / {t // 1024:,} KB" if t else f"다운로드 중... {d // 1024:,} KB"))
            self.install_progress.emit("VB-CABLE 설치 프로그램이 열렸습니다. 'Install Driver'를 눌러 설치를 마치세요.")
            return vbcable.run_setup(path)

        def done(code, err):
            self.install_btn.setEnabled(True)
            if err:
                QtWidgets.QMessageBox.warning(self, "VB-CABLE 설치", f"설치를 진행하지 못했습니다: {err}\n"
                                              "'VB-Audio 사이트' 버튼으로 직접 설치할 수 있습니다.")
            elif code is None:
                QtWidgets.QMessageBox.information(self, "VB-CABLE 설치", "관리자 권한 요청이 취소되었습니다.")
            self._refresh_status()
            if not err and code is not None:
                msg = ("설치가 끝났습니다. 장치가 제대로 동작하려면 컴퓨터를 재부팅하세요." if vbcable.is_installed()
                       else "설치 프로그램이 끝났지만 장치가 아직 보이지 않습니다. 재부팅한 뒤 다시 확인하세요.")
                QtWidgets.QMessageBox.information(self, "VB-CABLE 설치", msg)

        worker.finished.connect(done)
        worker.run(job)

    def apply(self):
        self.settings.set("audio.voice", {"monitor": self.monitor.isChecked(),
                                          "monitor_device": self.device.currentData() or "",
                                          "monitor_volume": self.volume.value()})
        self.settings.set("audio.mode", "voicechat")
        self.settings.save()
