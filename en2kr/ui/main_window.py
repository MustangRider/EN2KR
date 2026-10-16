"""Manager window: status, audio source picker, log, sensitivity and word lists."""
import html
import logging
import os

from PySide6 import QtCore, QtGui, QtWidgets

from .. import APP_NAME, __version__, gpu, paths
from ..audio import device as audio_device
from ..audio import sessions
from ..pipeline import Pipeline
from ..settings import DEFAULT_EDIT_KEY
from .api_dialog import ApiDialog
from .bridge import PipelineBridge, Worker
from .engine_dialog import EngineDialog
from .hotkeys import Hotkeys, key_label
from .onboarding import OnboardingDialog
from .overlay import OverlayWindow
from .settings_dialog import SettingsDialog
from .text_view import text_view
from .theme import STATE_COLORS

log = logging.getLogger(__name__)

HELP = {
    "vad": ("음성 감지 기준값",
            "소리가 '사람 목소리'인지 판정하는 기준입니다.<br><br>"
            "• 낮을수록: 작은 목소리도 잡지만 총소리·배경음도 말로 착각하기 쉽습니다.<br>"
            "• 높을수록: 확실한 목소리만 잡습니다.<br><br>권장: 0.30 ~ 0.50"),
    "silence": ("침묵 대기",
                "말이 멈춘 뒤 이 시간만큼 조용하면 한 문장이 끝난 것으로 봅니다.<br><br>"
                "• 짧을수록 빨리 번역되지만 문장이 잘게 끊깁니다.<br>"
                "• 길수록 문장이 온전하지만 반응이 늦습니다.<br><br>권장: 0.4 ~ 0.8초"),
    "maxlen": ("최대 발화 길이",
               "쉬지 않고 이어지는 말을 이 길이마다 끊어서 번역합니다. "
               "배경음 때문에 말이 끝나지 않은 것으로 판정될 때도 이 길이에서 끊깁니다."),
}


class HelpDialog(QtWidgets.QDialog):
    """Plain help popup. QMessageBox makes Windows play its alert sound on every '?' click."""

    def __init__(self, title, html_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(text_view(html_text), 1)
        ok = QtWidgets.QPushButton("확인")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        row.addWidget(ok)
        lay.addLayout(row)
        self.resize(480, 300)


class SourceCombo(QtWidgets.QComboBox):
    about_to_show = QtCore.Signal()

    def showPopup(self):
        self.about_to_show.emit()
        super().showPopup()


class TermEditor(QtWidgets.QWidget):
    """Two-column word list (glossary / slang) backed by a settings dict."""
    changed = QtCore.Signal()

    def __init__(self, settings, key, headers, placeholder, parent=None):
        super().__init__(parent)
        self.settings, self.key = settings, key
        lay = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout()
        self.src = QtWidgets.QLineEdit()
        self.src.setPlaceholderText(placeholder[0])
        self.dst = QtWidgets.QLineEdit()
        self.dst.setPlaceholderText(placeholder[1])
        add = QtWidgets.QPushButton("추가")
        add.clicked.connect(self._add)
        self.dst.returnPressed.connect(self._add)
        row.addWidget(self.src)
        row.addWidget(self.dst)
        row.addWidget(add)
        lay.addLayout(row)
        rm = QtWidgets.QPushButton("선택한 항목 삭제")
        rm.clicked.connect(self._remove)
        lay.addWidget(rm)
        self.refresh()

    def refresh(self):
        data = self.settings.get(self.key, {})
        self.table.setRowCount(0)
        for k, v in sorted(data.items(), key=lambda kv: kv[0].lower()):
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(k))
            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(v))

    def _add(self):
        k, v = self.src.text().strip(), self.dst.text().strip()
        if not k or not v:
            return
        data = self.settings.get(self.key, {})
        for existing in [e for e in data if e.lower() == k.lower()]:
            del data[existing]  # matching is case-insensitive; keep one spelling
        data[k] = v
        self.settings.set(self.key, data)
        self.settings.save()
        self.src.clear()
        self.dst.clear()
        self.refresh()
        self.changed.emit()

    def _remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        data = self.settings.get(self.key, {})
        for r in rows:
            data.pop(self.table.item(r, 0).text(), None)
        self.settings.set(self.key, data)
        self.settings.save()
        self.refresh()
        self.changed.emit()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1180, 820)
        self.bridge = PipelineBridge()
        self.pipeline = Pipeline(settings, self.bridge.events())
        self.overlay = OverlayWindow(settings)
        self.hotkeys = Hotkeys(settings.get("hotkeys.edit_mode", DEFAULT_EDIT_KEY))
        self.cuda = gpu.cuda_device_count() > 0
        self._lat = {"stt": None, "tr": None}
        self._save_timer = QtCore.QTimer(self, singleShot=True, interval=800, timeout=self.settings.save)
        self._build_ui()
        self._connect()

    # ================= UI =================
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        # status chips
        top = QtWidgets.QHBoxLayout()
        self.chips = {}
        for comp, title in (("audio", "오디오"), ("stt", "인식"), ("translate", "번역")):
            lab = QtWidgets.QLabel()
            lab.setTextFormat(QtCore.Qt.TextFormat.RichText)
            self.chips[comp] = (lab, title)
            self._set_chip(comp, "off", "대기")
            top.addWidget(lab)
            top.addSpacing(14)
        top.addStretch()
        top.addWidget(QtWidgets.QLabel("입력"))
        self.level = QtWidgets.QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.setFixedWidth(120)
        top.addWidget(self.level)
        self.lamp = QtWidgets.QLabel("● 음성")
        top.addWidget(self.lamp)
        self._set_lamp(False)
        root.addLayout(top)

        # source picker
        src = QtWidgets.QHBoxLayout()
        src.addWidget(QtWidgets.QLabel("번역할 프로그램:"))
        self.source = SourceCombo()
        self.source.setMinimumWidth(380)
        src.addWidget(self.source)
        refresh = QtWidgets.QPushButton("새로고침")
        refresh.clicked.connect(self._populate_sources)
        src.addWidget(refresh)
        self.source_hint = QtWidgets.QLabel()
        self.source_hint.setObjectName("muted")
        src.addWidget(self.source_hint)
        src.addStretch()
        root.addLayout(src)

        split = QtWidgets.QSplitter()
        root.addWidget(split, 1)

        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(600)
        self.log.setStyleSheet("QTextEdit { background:#0d0f12; font-family:'Consolas','Malgun Gothic'; font-size:10pt; }")
        ll.addWidget(self.log, 1)

        sens = QtWidgets.QGroupBox("감도")
        sl = QtWidgets.QGridLayout(sens)
        vad = self.settings.section("vad")
        self.sliders = {}
        specs = [("vad", "음성 감지 기준값", 5, 95, vad["threshold"] * 100, lambda v: f"{v / 100:.2f}"),
                 ("silence", "침묵 대기", 2, 30, vad["silence_sec"] * 10, lambda v: f"{v / 10:.1f}초"),
                 ("maxlen", "최대 발화 길이", 4, 30, vad["max_utterance_sec"], lambda v: f"{v}초")]
        for row, (key, title, lo, hi, value, fmt) in enumerate(specs):
            hb = QtWidgets.QPushButton("?")
            hb.setObjectName("help")
            hb.clicked.connect(lambda _=False, k=key: HelpDialog(*HELP[k], parent=self).exec())
            sl.addWidget(hb, row, 0)
            sl.addWidget(QtWidgets.QLabel(title), row, 1)
            s = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
            s.setRange(lo, hi)
            s.setValue(int(round(value)))
            val = QtWidgets.QLabel(fmt(s.value()))
            val.setMinimumWidth(48)
            s.valueChanged.connect(lambda v, k=key, l=val, f=fmt: self._slider_changed(k, v, l, f))
            sl.addWidget(s, row, 2)
            sl.addWidget(val, row, 3)
            self.sliders[key] = s
        sl.setColumnStretch(2, 1)
        ll.addWidget(sens)

        btns = QtWidgets.QHBoxLayout()
        for text, slot in (("인식 엔진", self.open_engine), ("번역 API", self.open_api),
                           ("오버레이·단축키·기록", self.open_settings), ("사용 안내", self.open_onboarding),
                           ("기록 폴더 열기", lambda: os.startfile(paths.app_dir()))):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch()
        edit_tip = QtWidgets.QLabel(f"{key_label(self.settings.get('hotkeys.edit_mode', DEFAULT_EDIT_KEY))}: 오버레이 편집 모드")
        edit_tip.setStyleSheet("color:#f2cc60; font-weight:bold;")
        self.edit_tip = edit_tip
        btns.addWidget(edit_tip)
        ll.addLayout(btns)
        split.addWidget(left)

        tabs = self.tabs = QtWidgets.QTabWidget()
        self.glossary = TermEditor(self.settings, "glossary", ["영어", "한국어(고정 번역)"], ("영어 단어/구", "한국어"))
        self.slang = TermEditor(self.settings, "slang", ["슬랭", "풀어쓴 영어"], ("예: nade", "예: grenade"))
        self._term_tabs = {}
        self._add_term_tab(self.glossary, "고유명사", "translate.use_glossary", "고유명사 고정 번역 사용",
                           "게임 고유명사를 항상 지정한 한국어로 번역합니다. 대소문자·복수형(s)은 자동 처리됩니다.")
        self._add_term_tab(self.slang, "슬랭", "translate.use_slang", "슬랭 풀어쓰기 사용",
                           "말로 하는 게임 은어(예: nade → grenade)를 번역 전에 풀어 써서 오역을 줄입니다.")
        tabs.addTab(self._build_hallucination_tab(), "환청 차단")
        split.addWidget(tabs)
        split.setSizes([700, 460])

        sb = self.statusBar()
        self.lat_label = QtWidgets.QLabel("지연: -")
        self.usage_label = QtWidgets.QLabel("")
        self.dl_bar = QtWidgets.QProgressBar()
        self.dl_bar.setFixedWidth(260)
        self.dl_bar.setFormat("모델 다운로드 %p%")
        self.dl_bar.hide()
        sb.addWidget(self.lat_label)
        sb.addPermanentWidget(self.dl_bar)
        sb.addPermanentWidget(self.usage_label)
        self._populate_sources()

    def _add_term_tab(self, editor, title, key, toggle_text, note):
        w = QtWidgets.QWidget()
        l = QtWidgets.QVBoxLayout(w)
        on = QtWidgets.QCheckBox(toggle_text)
        on.setChecked(bool(self.settings.get(key, True)))
        on.toggled.connect(lambda checked: self._toggle_terms(key, checked))
        l.addWidget(on)
        lab = QtWidgets.QLabel(note)
        lab.setObjectName("muted")
        lab.setWordWrap(True)
        l.addWidget(lab)
        l.addWidget(editor, 1)
        index = self.tabs.addTab(w, title)
        self._term_tabs[key] = (index, title, editor, toggle_text)
        self._show_term_state(key)

    def _show_term_state(self, key):
        index, title, editor, _ = self._term_tabs[key]
        on = bool(self.settings.get(key, True))
        self.tabs.setTabText(index, title if on else f"{title} (꺼짐)")
        editor.table.setEnabled(on)

    def _toggle_terms(self, key, on):
        self.settings.set(key, on)
        self.settings.save()
        self._show_term_state(key)
        self.pipeline.apply_translation()
        self._log_html(f"<span style='color:#58a6ff'>{self._term_tabs[key][3]}: {'켬' if on else '끔'}</span>")

    def _build_hallucination_tab(self):
        w = QtWidgets.QWidget()
        l = QtWidgets.QVBoxLayout(w)
        note = QtWidgets.QLabel("음성이 없는데 인식된 문장(환청)을 숨깁니다. 대소문자·끝 문장부호는 무시합니다.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        l.addWidget(note)
        self.hall_list = QtWidgets.QListWidget()
        l.addWidget(self.hall_list, 1)
        row = QtWidgets.QHBoxLayout()
        self.hall_in = QtWidgets.QLineEdit()
        self.hall_in.setPlaceholderText("차단할 문장")
        add = QtWidgets.QPushButton("차단 등록")
        add.clicked.connect(self._add_hall)
        self.hall_in.returnPressed.connect(self._add_hall)
        rm = QtWidgets.QPushButton("선택 해제")
        rm.clicked.connect(self._remove_hall)
        row.addWidget(self.hall_in, 1)
        row.addWidget(add)
        row.addWidget(rm)
        l.addLayout(row)
        g = QtWidgets.QGroupBox("자동 차단 (이번 실행에서만, 신뢰도 낮은 반복 문장)")
        gl = QtWidgets.QVBoxLayout(g)
        self.session_list = QtWidgets.QListWidget()
        self.session_list.setFixedHeight(90)
        gl.addWidget(self.session_list)
        sr = QtWidgets.QHBoxLayout()
        promote = QtWidgets.QPushButton("영구 차단으로 등록")
        promote.clicked.connect(self._promote_hall)
        unban = QtWidgets.QPushButton("차단 풀기")
        unban.clicked.connect(self._unban_hall)
        sr.addWidget(promote)
        sr.addWidget(unban)
        sr.addStretch()
        gl.addLayout(sr)
        l.addWidget(g)
        self._refresh_hall()
        return w

    # ================= wiring =================
    def _connect(self):
        b = self.bridge
        b.status.connect(self._on_status)
        b.level.connect(lambda rms: self.level.setValue(int(min(1.0, rms * 4) ** 0.5 * 100)))
        b.speech.connect(self._on_speech)
        b.stt.connect(self._on_stt)
        b.translation.connect(self._on_translation)
        b.notice.connect(self._on_notice)
        b.download.connect(self._on_download)
        b.auto_ban.connect(self._on_auto_ban)
        b.limit.connect(self.overlay.show_banner)
        self.source.activated.connect(self._source_chosen)
        self.source.about_to_show.connect(self._populate_sources)
        self.hotkeys.edit_toggled.connect(self._toggle_edit)
        self.hotkeys.font_up.connect(lambda: self.overlay.change_font(2))
        self.hotkeys.font_down.connect(lambda: self.overlay.change_font(-2))
        self.glossary.changed.connect(self.pipeline.apply_translation)
        self.slang.changed.connect(self.pipeline.apply_translation)
        self._usage_timer = QtCore.QTimer(self, interval=300_000, timeout=self._refresh_usage)

    def start(self):
        self.overlay.show()
        self.pipeline.start()
        self.hotkeys.start()
        if self.hotkeys.error:
            self._log_html(f"<span style='color:#f0a020'>전역 단축키를 등록하지 못했습니다: {html.escape(str(self.hotkeys.error))}</span>")
        self._usage_timer.start()
        QtCore.QTimer.singleShot(2000, self._refresh_usage)
        QtCore.QTimer.singleShot(300, self._first_run_checks)

    def _first_run_checks(self):
        s = self.settings
        if s.load_error:
            QtWidgets.QMessageBox.warning(self, "설정 파일 오류", s.load_error)
        if s.migrated_from:
            self._log_html(f"<span style='color:#58a6ff'>이전 버전 설정을 가져왔습니다: {html.escape(s.migrated_from)}</span>")
        for note in s.notes:
            self._log_html(f"<span style='color:#58a6ff'>{html.escape(note)}</span>")
        if s.notes:
            s.save()
        if not s.get("ui.onboarding_done"):
            self.open_onboarding()
            s.set("ui.onboarding_done", True)
            s.save()
        if not s.get("translate.order"):
            if QtWidgets.QMessageBox.question(self, "번역 API", "등록된 번역 API가 없습니다. 지금 설정할까요?") \
                    == QtWidgets.QMessageBox.StandardButton.Yes:
                self.open_api()
        if not self.cuda and s.get("stt.engine") != "cloud" and not s.get("ui.cpu_warning_dismissed"):
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("GPU 가속 없음")
            box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            box.setText("NVIDIA GPU를 사용할 수 없어 CPU로 음성을 인식합니다. 번역이 몇 초씩 늦을 수 있습니다.")
            box.setInformativeText("'인식 엔진'에서 실행 위치를 '클라우드 · Groq'로 바꾸면 GPU 없이도 빠르게 인식합니다 "
                                   "(무료 API 키 필요).\n\nNVIDIA 그래픽카드가 있다면 최신 그래픽 드라이버를 설치해 보세요. "
                                   "CUDA Toolkit은 설치할 필요가 없습니다.")
            cb = QtWidgets.QCheckBox("다시 보지 않기")
            box.setCheckBox(cb)
            box.exec()
            if cb.isChecked():
                s.set("ui.cpu_warning_dismissed", True)
                s.save()

    # ================= audio source =================
    def _populate_sources(self):
        audio = self.settings.section("audio")
        self.source.blockSignals(True)
        self.source.clear()
        apps = []
        try:
            apps = sessions.list_audio_apps()
        except Exception as e:
            log.warning("listing audio apps failed: %s", e)
        if audio.get("mode", "process") == "process" and not (audio.get("process_name") or ""):
            self.source.addItem("— 번역할 프로그램을 선택하세요 —", ("none", None))
        first_app = self.source.count()
        seen = set()
        saved = audio.get("process_name") or ""
        for a in apps:
            if a.name.lower() in seen:
                continue
            seen.add(a.name.lower())
            playing = "  ● 재생 중" if a.active and a.peak > 0.001 else ""
            self.source.addItem(a.label + playing, ("process", a.name))
        if saved and saved.lower() not in seen:
            self.source.insertItem(first_app, f"{saved}  (실행 대기)", ("process", saved))
        self.source.insertSeparator(self.source.count())
        if audio.get("mode") == "device" and audio.get("device_name"):
            self.source.addItem(f"입력 장치: {audio['device_name']}", ("device", audio["device_name"]))
        self.source.addItem("입력 장치 직접 선택 (고급)…", ("device_pick", None))
        mode = audio.get("mode")
        want = ("device", audio.get("device_name")) if mode == "device" else ("process", saved)
        for i in range(self.source.count()):
            d = self.source.itemData(i)
            if d and d[0] == want[0] and (d[1] or "").lower() == (want[1] or "").lower():
                self.source.setCurrentIndex(i)
                break
        self.source.blockSignals(False)
        self.source_hint.setText("선택된 입력 장치만 번역합니다" if mode == "device" else "선택된 프로그램만 번역합니다")

    def _source_chosen(self, index):
        kind, value = self.source.itemData(index) or (None, None)
        if kind == "process":
            self.settings.set("audio.mode", "process")
            self.settings.set("audio.process_name", value)
        elif kind in ("device", "device_pick"):
            if kind == "device_pick":
                devs = audio_device.list_input_devices()
                if not devs:
                    QtWidgets.QMessageBox.warning(self, "입력 장치", "사용할 수 있는 입력 장치가 없습니다.")
                    self._populate_sources()
                    return
                keys = [d["key"] for d in devs]
                value, ok = QtWidgets.QInputDialog.getItem(
                    self, "입력 장치 선택", "녹음할 장치:", keys, 0, False)
                if not ok:
                    self._populate_sources()
                    return
            self.settings.set("audio.mode", "device")
            self.settings.set("audio.device_name", value)
        else:
            return
        self.settings.save()
        self.pipeline.restart_source()
        self._populate_sources()

    # ================= pipeline events =================
    def _set_chip(self, comp, state, msg):
        lab, title = self.chips[comp]
        color = STATE_COLORS.get(state, "#888")
        lab.setText(f"<span style='color:{color}'>●</span> <b>{title}</b> {html.escape(msg)}")
        lab.setToolTip(msg)

    def _set_lamp(self, active):
        self.lamp.setStyleSheet(f"color: {'#ff4d4d' if active else '#555'}; font-weight: bold;")

    def _on_status(self, comp, state, msg):
        if comp in self.chips:
            self._set_chip(comp, state, msg)
        if comp == "stt" and state in ("ok", "wait", "error"):
            self.dl_bar.hide()
        if state == "error":
            self._log_html(f"<span style='color:#f85149'>[{comp}] {html.escape(msg)}</span>")

    def _on_speech(self, active):
        self._set_lamp(active)
        self.overlay.set_speaking(active)

    def _on_stt(self, line):
        if line.note:
            text = html.escape(line.raw) if line.raw else ""
            self._log_html(f"<span style='color:#6e7681'>[건너뜀 · {html.escape(line.note)}] {text}</span>")
            return
        self._ema("stt", line.latency)
        via = " <span style='color:#d29922'>[내 PC 대신]</span>" if line.via == "fallback" else ""
        self._log_html(f"<span style='color:#8b949e'>{QtCore.QTime.currentTime().toString('HH:mm:ss')}</span> "
                       f"<span style='color:#58a6ff'>[{line.language}]</span>{via} "
                       f"<span style='color:#3fb950'>{html.escape(line.text)}</span>")
        self.overlay.upsert_source(line.line_id, line.text, line.language)

    def _on_translation(self, res):
        self.overlay.set_translation(res.line_id, res.source, res.text, res.error)
        if res.error:
            self._log_html(f"<span style='color:#f85149'>&nbsp;&nbsp;→ {html.escape(res.error)}</span>")
            return
        if not res.cached:
            self._ema("tr", res.latency)
        src = "캐시" if res.cached else res.provider
        self._log_html(f"<span style='color:#e6e6e6'>&nbsp;&nbsp;→ {html.escape(res.text)}</span> "
                       f"<span style='color:#6e7681'>({html.escape(src or '')})</span>")

    def _on_notice(self, msg):
        self._log_html(f"<span style='color:#f0a020'>⚠ {html.escape(msg)}</span>")
        self.statusBar().showMessage(msg, 8000)

    def _on_download(self, done, total):
        self.dl_bar.show()
        if total:
            self.dl_bar.setRange(0, 1000)
            self.dl_bar.setValue(int(done / total * 1000))
            self.dl_bar.setFormat(f"모델 다운로드 {done / 2**20:,.0f} / {total / 2**20:,.0f} MB")
        else:
            self.dl_bar.setRange(0, 0)
            self.dl_bar.setFormat(f"모델 다운로드 {done / 2**20:,.0f} MB")

    def _on_auto_ban(self, text):
        self._log_html(f"<span style='color:#d29922'>자동 차단(이번 실행): {html.escape(text)} - 환청 차단 탭에서 풀 수 있습니다</span>")
        self._refresh_hall()

    def _ema(self, key, value):
        old = self._lat[key]
        self._lat[key] = value if old is None else old * 0.8 + value * 0.2
        parts = []
        if self._lat["stt"] is not None:
            parts.append(f"인식 {self._lat['stt']:.2f}초")
        if self._lat["tr"] is not None:
            parts.append(f"번역 {self._lat['tr']:.2f}초")
        self.lat_label.setText("평균 지연: " + " · ".join(parts))

    def _log_html(self, s):
        self.log.append(s)
        self.log.moveCursor(QtGui.QTextCursor.MoveOperation.End)

    def _refresh_usage(self):
        worker = Worker(self)

        def done(usage, err):
            if err or not usage:
                self.usage_label.setText("")
                return
            self.usage_label.setText(" · ".join(f"{k} {u:,} / {l:,}자 ({u / l:.0%})" for k, (u, l) in usage.items() if l))

        worker.finished.connect(done)
        worker.run(self.pipeline.translator.usage)

    # ================= controls =================
    def _slider_changed(self, key, value, label, fmt):
        label.setText(fmt(value))
        field = {"vad": ("vad.threshold", value / 100), "silence": ("vad.silence_sec", value / 10),
                 "maxlen": ("vad.max_utterance_sec", float(value))}[key]
        self.settings.set(*field)
        self.pipeline.apply_vad()
        self._save_timer.start()

    def _toggle_edit(self):
        active = self.overlay.toggle_edit_mode()
        self.hotkeys.set_edit_mode(active)

    def _refresh_hall(self):
        self.hall_list.clear()
        self.hall_list.addItems(sorted(self.settings.get("hallucinations", []), key=str.lower))
        self.session_list.clear()
        self.session_list.addItems(self.pipeline.filter.session_bans)

    def _add_hall(self):
        t = self.hall_in.text().strip()
        if not t:
            return
        items = self.settings.get("hallucinations", [])
        if t not in items:
            items.append(t)
            self.settings.set("hallucinations", items)
            self.settings.save()
            self.pipeline.apply_filters()
        self.hall_in.clear()
        self._refresh_hall()

    def _remove_hall(self):
        it = self.hall_list.currentItem()
        if not it:
            return
        items = [x for x in self.settings.get("hallucinations", []) if x != it.text()]
        self.settings.set("hallucinations", items)
        self.settings.save()
        self.pipeline.apply_filters()
        self._refresh_hall()

    def _promote_hall(self):
        it = self.session_list.currentItem()
        if it:
            self.hall_in.setText(it.text())
            self._add_hall()

    def _unban_hall(self):
        it = self.session_list.currentItem()
        if it:
            self.pipeline.filter.unban(it.text())
            self._refresh_hall()

    # ================= dialogs =================
    def open_engine(self):
        old = self.settings.section("stt")
        dlg = EngineDialog(old, self.cuda, self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        new = dlg.result_cfg()
        self.settings.set("stt", new)
        self.settings.save()
        if EngineDialog.needs_reload(old, new):
            self.pipeline.reload_stt(clear_fallback=True)
        else:
            self.pipeline.apply_stt_options()
            self._log_html("<span style='color:#58a6ff'>인식 언어 설정을 적용했습니다.</span>")

    def open_api(self):
        dlg = ApiDialog(self.settings, self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        missing = dlg.apply()
        self.pipeline.apply_translation()
        self._refresh_usage()
        if missing:
            QtWidgets.QMessageBox.warning(self, "번역 API", "키(또는 모델)가 비어 있어 사용하지 않는 API: " + ", ".join(missing))

    def open_settings(self):
        old_key = self.settings.get("hotkeys.edit_mode", DEFAULT_EDIT_KEY)
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        dlg.apply()
        self.overlay.apply_settings()
        self.pipeline.apply_logging()
        new_key = self.settings.get("hotkeys.edit_mode", DEFAULT_EDIT_KEY)
        if new_key != old_key:
            self.hotkeys.set_edit_key(new_key)
            self.edit_tip.setText(f"{key_label(new_key)}: 오버레이 편집 모드")
            if self.hotkeys.error:
                self._log_html(f"<span style='color:#f0a020'>단축키 '{html.escape(new_key)}'를 등록하지 못했습니다: "
                               f"{html.escape(str(self.hotkeys.error))}</span>")

    def open_onboarding(self):
        dlg = OnboardingDialog(self.settings.get("hotkeys.edit_mode", DEFAULT_EDIT_KEY), self)
        dlg.open_api.connect(self.open_api)
        dlg.open_engine.connect(self.open_engine)
        dlg.exec()

    # ================= shutdown =================
    def closeEvent(self, event):
        try:
            self.settings.save()
        except OSError as e:
            log.error("Saving settings failed: %s", e)
        self.hotkeys.stop()
        self.pipeline.stop()
        self.overlay.close()
        event.accept()
        QtWidgets.QApplication.quit()
