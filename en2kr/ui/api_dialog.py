"""Translation API settings: which services to use, in which order, and their keys."""
import time
import webbrowser

from PySide6 import QtCore, QtWidgets

from ..settings import OPENAI_PRESETS
from ..translate import providers as P
from .bridge import Worker

INFO = {
    "deepl": ("월 50만 자 무료. 번역 품질이 좋습니다.", "https://www.deepl.com/pro-api"),
    "azure": ("월 200만 자 무료(F0 등급). 지역(region)도 입력해야 합니다.",
              "https://learn.microsoft.com/azure/ai-services/translator/create-translator-resource"),
    "google": ("월 50만 자 무료. Google Cloud 결제 계정 연결과 'Cloud Translation API' 사용 설정이 필요합니다.",
               "https://cloud.google.com/translate/docs/setup"),
    "openai": ("Gemini·Groq·OpenRouter·Ollama 등 OpenAI 호환 API. 슬랭과 문맥에 강하지만 느립니다. "
               "무료 한도는 서비스마다 자주 바뀌고, 무료 티어는 입력이 학습에 쓰일 수 있습니다.", None),
}
TEST_SENTENCE = "Enemy behind the gate on the left, don't shoot, we're friendly!"


class ApiDialog(QtWidgets.QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("번역 API 설정")
        self.resize(780, 480)
        self.settings = settings
        tr = settings.section("translate")
        self.providers_cfg = tr["providers"]
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel("체크한 API를 위에서부터 순서대로 사용하고, 한도 초과·오류가 나면 다음 API로 자동 전환합니다.")
        intro.setObjectName("muted")
        layout.addWidget(intro)

        body = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        self.list = QtWidgets.QListWidget()
        self.list.setFixedWidth(230)
        order = [p for p in tr.get("order", []) if p in P.PROVIDERS]
        for pid in order + [p for p in P.PROVIDERS if p not in order]:
            it = QtWidgets.QListWidgetItem(P.PROVIDERS[pid].label)
            it.setData(QtCore.Qt.ItemDataRole.UserRole, pid)
            it.setFlags(it.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.CheckState.Checked if pid in order else QtCore.Qt.CheckState.Unchecked)
            self.list.addItem(it)
        left.addWidget(self.list, 1)
        mv = QtWidgets.QHBoxLayout()
        up, down = QtWidgets.QPushButton("▲ 위로"), QtWidgets.QPushButton("▼ 아래로")
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        mv.addWidget(up)
        mv.addWidget(down)
        left.addLayout(mv)
        body.addLayout(left)

        self.stack = QtWidgets.QStackedWidget()
        self.forms = {}
        for pid in P.PROVIDERS:
            self.stack.addWidget(self._build_form(pid))
        body.addWidget(self.stack, 1)
        layout.addLayout(body, 1)

        self.slang = QtWidgets.QCheckBox("게임 은어 풀어쓰기 사용 (nade → grenade 등, 메인 창 '슬랭' 탭에서 편집)")
        self.slang.setChecked(tr.get("use_slang", True))
        layout.addWidget(self.slang)
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save
                                          | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText("저장")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self.list.currentRowChanged.connect(self._show_current)
        self.list.setCurrentRow(0)

    # ---------- forms ----------
    def _build_form(self, pid):
        cfg = self.providers_cfg.get(pid, {})
        w = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(w)
        fields = {}
        title = QtWidgets.QLabel(f"<b>{P.PROVIDERS[pid].label}</b>")
        form.addRow(title)
        info, url = INFO[pid]
        lab = QtWidgets.QLabel(info)
        lab.setWordWrap(True)
        lab.setObjectName("muted")
        form.addRow(lab)
        if pid == "openai":
            preset = QtWidgets.QComboBox()
            for key, p in OPENAI_PRESETS.items():
                preset.addItem(p["label"], key)
            preset.setCurrentIndex(max(0, preset.findData(cfg.get("preset", "gemini"))))
            base = QtWidgets.QLineEdit(cfg.get("base_url", ""))
            model = QtWidgets.QComboBox()
            model.setEditable(True)
            model.setCurrentText(cfg.get("model", ""))
            model.lineEdit().setPlaceholderText("예: gemini-2.5-flash-lite (목록 불러오기 권장)")
            fetch = QtWidgets.QPushButton("모델 목록 불러오기")
            row = QtWidgets.QHBoxLayout()
            row.addWidget(model, 1)
            row.addWidget(fetch)
            preset.currentIndexChanged.connect(
                lambda _i, pr=preset, b=base: b.setText(OPENAI_PRESETS[pr.currentData()]["base_url"])
                if pr.currentData() != "custom" else None)
            fetch.clicked.connect(lambda: self._fetch_models(pid))
            form.addRow("서비스:", preset)
            form.addRow("API 주소:", base)
            form.addRow("모델:", row)
            fields.update(preset=preset, base_url=base, model=model)
        key = QtWidgets.QLineEdit(cfg.get("key", ""))
        key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        show = QtWidgets.QCheckBox("보기")
        show.toggled.connect(lambda on, k=key: k.setEchoMode(
            QtWidgets.QLineEdit.EchoMode.Normal if on else QtWidgets.QLineEdit.EchoMode.Password))
        krow = QtWidgets.QHBoxLayout()
        krow.addWidget(key, 1)
        krow.addWidget(show)
        form.addRow("API 키:", krow)
        fields["key"] = key
        if pid == "azure":
            region = QtWidgets.QLineEdit(cfg.get("region", "koreacentral"))
            form.addRow("지역(region):", region)
            fields["region"] = region
        btn_row = QtWidgets.QHBoxLayout()
        test = QtWidgets.QPushButton("연결 테스트")
        test.clicked.connect(lambda: self._test(pid))
        btn_row.addWidget(test)
        if url:
            guide = QtWidgets.QPushButton("키 발급 안내")
            guide.clicked.connect(lambda: webbrowser.open(url))
            btn_row.addWidget(guide)
        btn_row.addStretch()
        form.addRow(btn_row)
        result = QtWidgets.QLabel("")
        result.setWordWrap(True)
        result.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(result)
        fields["result"] = result
        self.forms[pid] = fields
        return w

    def _show_current(self, row):
        if row >= 0:
            pid = self.list.item(row).data(QtCore.Qt.ItemDataRole.UserRole)
            self.stack.setCurrentIndex(list(P.PROVIDERS).index(pid))

    def _move(self, delta):
        row = self.list.currentRow()
        new = row + delta
        if 0 <= row < self.list.count() and 0 <= new < self.list.count():
            it = self.list.takeItem(row)
            self.list.insertItem(new, it)
            self.list.setCurrentRow(new)

    def _form_cfg(self, pid):
        f = self.forms[pid]
        cfg = dict(self.providers_cfg.get(pid, {}))
        cfg["key"] = f["key"].text().strip()
        if "region" in f:
            cfg["region"] = f["region"].text().strip()
        if pid == "openai":
            cfg["preset"] = f["preset"].currentData()
            cfg["base_url"] = f["base_url"].text().strip()
            cfg["model"] = f["model"].currentText().strip()
        return cfg

    # ---------- actions ----------
    def _test(self, pid):
        prov = P.create(pid, self._form_cfg(pid))
        label = self.forms[pid]["result"]
        if not prov.configured:
            label.setText("<span style='color:#f0a020'>키(와 모델)를 먼저 입력하세요.</span>")
            return
        label.setText("테스트 중...")
        worker = Worker(self)

        def call():
            t0 = time.perf_counter()
            out = prov.translate(TEST_SENTENCE, "en")
            return out, time.perf_counter() - t0

        def done(res, err):
            if err:
                label.setText(f"<span style='color:#f85149'>실패: {err}</span>")
            else:
                label.setText(f"<span style='color:#3fb950'>성공 ({res[1]:.2f}초)</span><br>{TEST_SENTENCE}<br>→ {res[0]}")

        worker.finished.connect(done)
        worker.run(call)

    def _fetch_models(self, pid):
        prov = P.create(pid, self._form_cfg(pid))
        label, combo = self.forms[pid]["result"], self.forms[pid]["model"]
        label.setText("모델 목록을 불러오는 중...")
        worker = Worker(self)

        def done(models, err):
            if err:
                label.setText(f"<span style='color:#f85149'>목록을 불러오지 못했습니다: {err}</span>")
                return
            current = combo.currentText()
            combo.clear()
            combo.addItems(models)
            combo.setCurrentText(current or (models[0] if models else ""))
            label.setText(f"모델 {len(models)}개를 불러왔습니다. 번역에는 'flash-lite', 'mini', 'instant' 같은 "
                          "가볍고 빠른 모델을 추천합니다.")

        worker.finished.connect(done)
        worker.run(prov.list_models)

    def apply(self):
        order = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            pid = it.data(QtCore.Qt.ItemDataRole.UserRole)
            self.settings.set(f"translate.providers.{pid}", self._form_cfg(pid))
            if it.checkState() == QtCore.Qt.CheckState.Checked:
                order.append(pid)
        self.settings.set("translate.order", order)
        self.settings.set("translate.use_slang", self.slang.isChecked())
        self.settings.save()
        unconfigured = [P.PROVIDERS[p].label for p in order if not P.create(p, self._form_cfg(p)).configured]
        return unconfigured
