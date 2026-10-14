"""Speech recognition engine picker.

Step 1: engine type (English-only / multilingual fixed language / multilingual
auto-detect). Step 2: model size, shown with measured VRAM and speed next to
the GPU's current free VRAM.
"""
from PySide6 import QtCore, QtGui, QtWidgets

import time

from .. import gpu, stt
from . import key_guides
from .bridge import Worker

MODES = [
    ("english", "영어 전용", "영어만 들을 때. 가장 정확하고 빠릅니다."),
    ("fixed", "다국어 · 언어 고정", "특정 한 언어만 들을 때 (예: 독일어 서버)."),
    ("auto", "다국어 · 자동 감지", "여러 언어가 섞일 때. 허용한 언어 중에서만 판별합니다. "
                                "짧은 말이나 억양이 강하면 오판할 수 있습니다."),
]


def _speed_label(rtf):
    if rtf is None:
        return "-"
    if rtf <= 0.04:
        return f"매우 빠름 ({rtf:.2f})"
    if rtf <= 0.08:
        return f"빠름 ({rtf:.2f})"
    if rtf <= 0.16:
        return f"보통 ({rtf:.2f})"
    return f"느림 ({rtf:.2f})"


class EngineDialog(QtWidgets.QDialog):
    def __init__(self, stt_cfg, cuda=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("음성 인식 엔진 설정")
        self.resize(860, 640)
        self.cfg = dict(stt_cfg)
        self.cuda = cuda
        self.stats = stt.load_stats()
        layout = QtWidgets.QVBoxLayout(self)

        # ---- where recognition runs ----
        g0 = QtWidgets.QGroupBox("실행 위치")
        l0 = QtWidgets.QVBoxLayout(g0)
        where = QtWidgets.QHBoxLayout()
        self.local_rb = QtWidgets.QRadioButton("내 PC (GPU/CPU)")
        self.cloud_rb = QtWidgets.QRadioButton("클라우드 · Groq (무료 API 키, VRAM 사용 없음)")
        where.addWidget(self.local_rb)
        where.addWidget(self.cloud_rb)
        where.addStretch()
        l0.addLayout(where)
        self.cloud_box = QtWidgets.QWidget()
        cf = QtWidgets.QFormLayout(self.cloud_box)
        cf.setContentsMargins(0, 0, 0, 0)
        cloud_cfg = self.cfg.get("cloud") or {}
        self.cloud_key = QtWidgets.QLineEdit(cloud_cfg.get("key", ""))
        self.cloud_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.cloud_key.setPlaceholderText("gsk_... (아래 '키 발급 안내' 참고)")
        self.cloud_model = QtWidgets.QComboBox()
        for mid, desc in stt.CLOUD_MODELS.items():
            self.cloud_model.addItem(f"{mid} - {desc}", mid)
        self.cloud_model.setCurrentIndex(max(0, self.cloud_model.findData(cloud_cfg.get("model", "whisper-large-v3"))))
        test_row = QtWidgets.QHBoxLayout()
        self.cloud_test = QtWidgets.QPushButton("연결 테스트")
        self.cloud_test.clicked.connect(self._test_cloud)
        self.cloud_result = QtWidgets.QLabel("")
        self.cloud_result.setWordWrap(True)
        test_row.addWidget(self.cloud_test)
        guide = QtWidgets.QPushButton("키 발급 안내")
        guide.clicked.connect(lambda: key_guides.show_groq(self))
        test_row.addWidget(guide)
        test_row.addWidget(self.cloud_result, 1)
        cf.addRow("Groq API 키:", self.cloud_key)
        cf.addRow("모델:", self.cloud_model)
        cf.addRow(test_row)
        note = QtWidgets.QLabel("무료 한도: 하루 2,000문장 · 분당 20문장 (문장당 최소 10초로 계산). 문장당 약 0.3~0.9초. "
                                "말소리 구간이 Groq 서버로 전송됩니다. 언어는 Groq가 자동으로 판별합니다.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        cf.addRow(note)
        l0.addWidget(self.cloud_box)
        layout.addWidget(g0)

        # ---- step 1 ----
        g1 = self.g1 = QtWidgets.QGroupBox("1단계 · 엔진 종류")
        l1 = QtWidgets.QGridLayout(g1)
        self.mode_buttons = QtWidgets.QButtonGroup(self)
        self._local_only = []  # step-1 widgets that only matter for the local engine
        for row, (mode, title, desc) in enumerate(MODES):
            rb = QtWidgets.QRadioButton(title)
            rb.setProperty("mode", mode)
            self.mode_buttons.addButton(rb, row)
            l1.addWidget(rb, row, 0)
            d = QtWidgets.QLabel(desc)
            d.setObjectName("muted")
            d.setWordWrap(True)
            l1.addWidget(d, row, 1)
            self._local_only += [rb, d]
        self.lang_combo = QtWidgets.QComboBox()
        for code, name in stt.LANGUAGES.items():
            if code != "ko":
                self.lang_combo.addItem(f"{name} ({code})", code)
        lang_label = QtWidgets.QLabel("인식 언어 / 판별이 애매할 때 기본 언어:")
        l1.addWidget(lang_label, 3, 0)
        l1.addWidget(self.lang_combo, 3, 1)
        self.auto_box = QtWidgets.QWidget()
        al = QtWidgets.QHBoxLayout(self.auto_box)
        al.setContentsMargins(0, 0, 0, 0)
        self.auto_list = QtWidgets.QListWidget()
        self.auto_list.setFlow(QtWidgets.QListView.Flow.LeftToRight)
        self.auto_list.setWrapping(True)
        self.auto_list.setFixedHeight(64)
        for code, name in stt.LANGUAGES.items():
            it = QtWidgets.QListWidgetItem(f"{name}")
            it.setData(QtCore.Qt.ItemDataRole.UserRole, code)
            it.setFlags(it.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.CheckState.Checked if code in self.cfg.get("auto_languages", [])
                             else QtCore.Qt.CheckState.Unchecked)
            self.auto_list.addItem(it)
        al.addWidget(self.auto_list, 1)
        self.skip_ko = QtWidgets.QCheckBox("한국어 음성은 번역하지 않고 건너뛰기 (한국인 파티원·한국어 방송 등)")
        self.skip_ko.setChecked(self.cfg.get("skip_korean", True))
        auto_label = QtWidgets.QLabel("자동 감지 허용 언어:")
        l1.addWidget(auto_label, 4, 0)
        self._local_only += [lang_label, self.lang_combo, auto_label, self.auto_box]
        l1.addWidget(self.auto_box, 4, 1)
        l1.addWidget(self.skip_ko, 5, 0, 1, 2)
        self.skip_note = QtWidgets.QLabel()
        self.skip_note.setObjectName("muted")
        self.skip_note.setWordWrap(True)
        l1.addWidget(self.skip_note, 6, 0, 1, 2)
        l1.setColumnStretch(1, 1)
        layout.addWidget(g1)

        # ---- step 2 ----
        g2 = self.g2 = QtWidgets.QGroupBox("2단계 · 모델 크기")
        l2 = QtWidgets.QVBoxLayout(g2)
        self.gpu_label = QtWidgets.QLabel()
        l2.addWidget(self.gpu_label)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["모델", "크기", "특징", "VRAM (실측)", "속도 (처리시간/음성길이)", "상태"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for c in (0, 1, 3, 4, 5):
            hh.setSectionResizeMode(c, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        l2.addWidget(self.table, 1)
        measured_on = self.stats.get("gpu") or "개발 PC"
        note = QtWidgets.QLabel(
            f"VRAM·속도는 {measured_on}에서 float16으로 실측한 값입니다 (CUDA 기본 사용량 포함). "
            "GPU마다 다를 수 있고, CPU 모드에서는 VRAM을 쓰지 않는 대신 몇 배 느립니다. ★ = 추천")
        note.setObjectName("muted")
        note.setWordWrap(True)
        l2.addWidget(note)
        opts = QtWidgets.QHBoxLayout()
        self.device_combo = QtWidgets.QComboBox()
        self.device_combo.addItem("자동 (GPU 우선)", "auto")
        self.device_combo.addItem("CPU 강제", "cpu")
        self.device_combo.setCurrentIndex(1 if self.cfg.get("device") == "cpu" else 0)
        opts.addWidget(QtWidgets.QLabel("실행 장치:"))
        opts.addWidget(self.device_combo)
        opts.addStretch()
        l2.addLayout(opts)
        layout.addWidget(g2, 1)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                          | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("적용")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        mode = self.cfg.get("mode", "english")
        idx = next((i for i, (m, _, _) in enumerate(MODES) if m == mode), 0)
        self.mode_buttons.button(idx).setChecked(True)
        i = self.lang_combo.findData(self.cfg.get("language", "en"))
        self.lang_combo.setCurrentIndex(max(0, i))
        self.mode_buttons.idToggled.connect(lambda *_: self._refresh())
        (self.cloud_rb if self.cfg.get("engine") == "cloud" else self.local_rb).setChecked(True)
        self.cloud_rb.toggled.connect(lambda *_: self._refresh())
        self._gpu_timer = QtCore.QTimer(self, interval=1500, timeout=self._update_gpu)
        self._gpu_timer.start()
        self._refresh()

    # ---------- helpers ----------
    def mode(self):
        return self.mode_buttons.checkedButton().property("mode")

    def _refresh(self):
        mode = self.mode()
        cloud = self.cloud_rb.isChecked()
        self.cloud_box.setVisible(cloud)
        self.g2.setVisible(not cloud)
        for w in self._local_only:
            w.setVisible(not cloud)
        self.g1.setTitle("옵션" if cloud else "1단계 · 엔진 종류")
        self.lang_combo.setEnabled(mode != "english")
        self.auto_box.setEnabled(mode == "auto")
        if cloud:
            self.skip_note.setText("받아 적은 결과가 한글이면 건너뜁니다. 한국어 억양이 섞인 영어는 대부분 영어로 적히므로 살아남습니다.")
            return
        self.skip_note.setText(
            "영어 전용 엔진은 언어를 구분하지 못해 한국어를 영어로 바꿔 받아 적습니다. 이 옵션을 켜면 "
            "언어 판별용 작은 모델(tiny)을 함께 써서 한국어를 걸러냅니다 (VRAM 약 100MB 추가)."
            if mode == "english" else
            "Whisper가 판별한 한국어 확률이 높을 때만 건너뜁니다. 억양이 강한 영어는 최대한 살려 둡니다.")
        kind = "english" if mode == "english" else "multilingual"
        current = self.selected_model() or stt.resolve_model_id(self.cfg, self.cuda)
        self.table.setRowCount(0)
        for m in stt.CATALOG:
            if m.kind != kind:
                continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            name = QtWidgets.QTableWidgetItem(("★ " if m.recommended else "") + m.label)
            name.setData(QtCore.Qt.ItemDataRole.UserRole, m.id)
            vram = QtWidgets.QTableWidgetItem(f"{m.vram_mb:,} MB" if m.vram_mb else "-")
            vram.setData(QtCore.Qt.ItemDataRole.UserRole, m.vram_mb or 0)
            state = "설치됨" if stt.is_downloaded(m.id) else f"다운로드 {m.download_gb:.1f} GB"
            for c, item in enumerate([name, QtWidgets.QTableWidgetItem(m.params), QtWidgets.QTableWidgetItem(m.desc),
                                      vram, QtWidgets.QTableWidgetItem(_speed_label(m.rtf)),
                                      QtWidgets.QTableWidgetItem(state)]):
                self.table.setItem(r, c, item)
            if m.id == current:
                self.table.selectRow(r)
        if not self.table.selectedItems() and self.table.rowCount():
            rec = next((r for r in range(self.table.rowCount())
                        if self.table.item(r, 0).text().startswith("★")), 0)
            self.table.selectRow(rec)
        self._update_gpu()

    def _update_gpu(self):
        info = gpu.gpu_info()
        if not self.cuda or info is None:
            self.gpu_label.setText("GPU: 사용 불가 → CPU로 실행 (VRAM 미사용, 속도 느림)")
            return
        self.gpu_label.setText(f"GPU: {info.name} · 사용 중 {info.used_mb / 1024:.1f} / {info.total_mb / 1024:.1f} GB · "
                               f"<b>여유 {info.free_mb / 1024:.1f} GB</b> (게임을 켠 상태에서 보면 가장 정확합니다)")
        for r in range(self.table.rowCount()):
            need = self.table.item(r, 3).data(QtCore.Qt.ItemDataRole.UserRole) or 0
            tight = need and need > info.free_mb
            for c in range(self.table.columnCount()):
                self.table.item(r, c).setForeground(QtGui.QColor("#f0a020") if tight else QtGui.QColor("#e6e6e6"))
            self.table.item(r, 3).setToolTip("현재 여유 VRAM보다 큽니다. 게임 성능이 떨어지거나 로드에 실패할 수 있습니다."
                                             if tight else "")

    def selected_model(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).data(QtCore.Qt.ItemDataRole.UserRole)

    def _test_cloud(self):
        key = self.cloud_key.text().strip()
        if not key:
            self.cloud_result.setText("<span style='color:#f0a020'>키를 먼저 입력하세요. 없으면 '키 발급 안내'를 보세요.</span>")
            return
        self.cloud_result.setText("테스트 중...")
        client = stt.GroqStt(key, self.cloud_model.currentData())
        worker = Worker(self)

        def call():
            client.check()
            import numpy as np
            t0 = time.perf_counter()
            client.transcribe(np.zeros(16000, np.float32))
            return time.perf_counter() - t0, client.remaining

        def done(res, err):
            if err:
                self.cloud_result.setText(f"<span style='color:#f85149'>실패: {err}</span>")
                return
            latency, remaining = res
            left = f" · 오늘 남은 요청 {remaining:,}" if remaining is not None else ""
            self.cloud_result.setText(f"<span style='color:#3fb950'>연결 성공 · 응답 {latency:.2f}초{left}</span>")

        worker.finished.connect(done)
        worker.run(call)

    def result_cfg(self):
        cfg = dict(self.cfg)
        cfg["engine"] = "cloud" if self.cloud_rb.isChecked() else "local"
        cfg["cloud"] = {"provider": "groq", "model": self.cloud_model.currentData(),
                        "key": self.cloud_key.text().strip()}
        cfg["mode"] = self.mode()
        cfg["language"] = self.lang_combo.currentData() or "en"
        cfg["auto_languages"] = [self.auto_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole)
                                 for i in range(self.auto_list.count())
                                 if self.auto_list.item(i).checkState() == QtCore.Qt.CheckState.Checked]
        cfg["skip_korean"] = self.skip_ko.isChecked()
        cfg["model"] = self.selected_model() or cfg.get("model", "auto")
        cfg["device"] = self.device_combo.currentData()
        return cfg

    @staticmethod
    def needs_reload(old, new):
        # skip_korean decides whether the Korean detector model is loaded next to an English-only model
        return any(old.get(k) != new.get(k) for k in ("engine", "cloud", "model", "device", "skip_korean"))
