"""
EN2KR4GAMING - Overlay module
Provides OverlayWindow: the transparent always-on-top subtitle overlay.
"""

import os
import json
import re
import time
import queue
import threading

import requests
import deepl
from PySide6 import QtWidgets, QtCore, QtGui

try:
    import win32gui
    import win32con
except ImportError:
    win32gui = None

from en2kr.config import (
    AppConfig,
    save_settings,
    SETTINGS_FILE,
    MAX_DISPLAY_LINES,
    MAX_HISTORY_LIMIT,
)


class OverlayWindow(QtWidgets.QWidget):
    recognition_signal = QtCore.Signal(str)
    vad_status_signal = QtCore.Signal(bool)
    update_signal = QtCore.Signal(str, str, bool, str)  # en, kr, is_complete, mode

    def __init__(self, config: AppConfig, main_win):
        super().__init__()
        self.config = config
        self.main_win = main_win
        self.is_edit_mode = False
        self.is_speaking = False
        self.text_lines = []
        self.font_size = 18
        self.line_spacing = 12  # 줄 간격 (기본값 12px)
        self.max_display_lines = MAX_DISPLAY_LINES  # 최대 표시 줄 수
        self.scroll_offset = 0
        self.pending_buffer = ""
        self.last_recognition_time = time.time()
        self.dragging = False
        self.offset = QtCore.QPoint()
        self.translator_deepl = None
        self.setWindowFlags(
            QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    s = json.load(f)
                    self.setGeometry(
                        s.get("x", 100), s.get("y", 100),
                        s.get("w", 800), s.get("h", 250)
                    )
                    self.font_size = s.get("font_size", 18)
                    self.line_spacing = s.get("line_spacing", 12)
                    self.max_display_lines = s.get("max_display_lines", MAX_DISPLAY_LINES)
            except Exception:
                self.setGeometry(100, 100, 800, 250)
        else:
            self.setGeometry(100, 100, 800, 250)

        self.font = QtGui.QFont("Arial", self.font_size, QtGui.QFont.Weight.Bold)
        self.grip = QtWidgets.QSizeGrip(self)
        self.grip.setFixedSize(30, 30)
        self.grip.hide()
        self.recognition_signal.connect(self.enqueue_recognition)
        self.vad_status_signal.connect(self.update_vad_status)
        self.update_signal.connect(self.update_ui)  # 번역 결과 업데이트 시그널 연결
        self.flush_timer = QtCore.QTimer()
        self.flush_timer.timeout.connect(self.check_context_flush)
        self.flush_timer.start(500)

        threading.Thread(target=self.translation_worker, daemon=True).start()
        self.show()
        QtCore.QTimer.singleShot(500, lambda: self.set_click_through(True))

    def wheelEvent(self, event):
        if self.is_edit_mode:
            delta = event.angleDelta().y()
            modifiers = event.modifiers()

            # Ctrl + 휠: 최대 표시 줄 수 조절
            if modifiers & QtCore.Qt.KeyboardModifier.ControlModifier:
                if delta > 0:
                    self.change_max_lines(1)   # 휠업: 증가
                else:
                    self.change_max_lines(-1)  # 휠다운: 감소
            # Shift + 휠: 줄 간격 조절
            elif modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier:
                if delta > 0:
                    self.change_line_spacing(2)   # 휠업: 증가
                else:
                    self.change_line_spacing(-2)  # 휠다운: 감소
            # 일반 휠: 스크롤
            else:
                if delta > 0:
                    if self.scroll_offset < len(self.text_lines) - self.max_display_lines:
                        self.scroll_offset += 1
                else:
                    if self.scroll_offset > 0:
                        self.scroll_offset -= 1
            self.update()

    def resizeEvent(self, event):
        self.grip.move(self.width() - self.grip.width(), self.height() - self.grip.height())
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        if self.is_edit_mode and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.dragging = True
            self.offset = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.move(self.mapToGlobal(event.position().toPoint() - self.offset))

    def mouseReleaseEvent(self, event):
        self.dragging = False
        save_settings(self.config, SETTINGS_FILE, overlay_geo=self.geometry(), font_size=self.font_size)

    def check_context_flush(self):
        if self.pending_buffer and (
            time.time() - self.last_recognition_time > self.config.context_timeout
        ):
            txt = self.pending_buffer
            self.pending_buffer = ""
            self.config.translation_queue.put((txt, True, "UPDATE"))

    def set_click_through(self, enabled):
        if not win32gui:
            return
        hwnd = int(self.winId())
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if enabled:
            style |= win32con.WS_EX_TRANSPARENT | win32con.WS_EX_LAYERED
        else:
            style &= ~win32con.WS_EX_TRANSPARENT
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)

    def toggle_edit_mode(self):
        self.is_edit_mode = not self.is_edit_mode
        self.set_click_through(not self.is_edit_mode)
        if not self.is_edit_mode:
            self.scroll_offset = 0
            self.grip.hide()
            save_settings(
                self.config, SETTINGS_FILE,
                overlay_geo=self.geometry(),
                font_size=self.font_size,
                line_spacing=self.line_spacing,
                max_display_lines=self.max_display_lines,
            )
        else:
            self.grip.show()
            self.grip.raise_()
        self.update()

    def change_font_size(self, delta):
        if self.is_edit_mode:
            self.font_size = max(10, self.font_size + delta)
            self.font.setPointSize(self.font_size)
            self.update()
            save_settings(
                self.config, SETTINGS_FILE,
                overlay_geo=self.geometry(),
                font_size=self.font_size,
                line_spacing=self.line_spacing,
                max_display_lines=self.max_display_lines,
            )

    def change_max_lines(self, delta):
        """Ctrl + -/+ : 최대 표시 줄 수 조절"""
        if self.is_edit_mode:
            self.max_display_lines = max(1, min(20, self.max_display_lines + delta))
            self.update()
            save_settings(
                self.config, SETTINGS_FILE,
                overlay_geo=self.geometry(),
                font_size=self.font_size,
                line_spacing=self.line_spacing,
                max_display_lines=self.max_display_lines,
            )

    def change_line_spacing(self, delta):
        """Shift + -/+ : 줄 간격 조절"""
        if self.is_edit_mode:
            self.line_spacing = max(0, min(40, self.line_spacing + delta))
            self.update()
            save_settings(
                self.config, SETTINGS_FILE,
                overlay_geo=self.geometry(),
                font_size=self.font_size,
                line_spacing=self.line_spacing,
                max_display_lines=self.max_display_lines,
            )

    def translation_worker(self):
        self.translator_deepl = None
        last_init_time = 0
        last_api_selected = None

        def init_deepl():
            try:
                if not self.config.selected_api:
                    raise RuntimeError("선택된 API가 없습니다.")
                if not self.config.api_key or not self.config.api_key.strip():
                    raise ValueError(f"{self.config.selected_api} API 키가 설정되지 않았습니다.")

                if self.config.selected_api == "DeepL":
                    self.translator_deepl = deepl.Translator(self.config.api_key)
                    # 간단한 테스트로 API 키 유효성 확인
                    test_result = self.translator_deepl.translate_text("test", target_lang="KO")
                    if not test_result or not hasattr(test_result, 'text'):
                        raise RuntimeError("DeepL API 응답이 올바르지 않습니다.")
                    print(f"[TRANS] DeepL Translator 초기화 성공")
                    return True
            except Exception as e:
                self.translator_deepl = None
                error_msg = f"[{self.config.selected_api}] 초기화 실패: {str(e)}"
                try:
                    self.main_win.log_signal.emit("TRANS", "FAIL", error_msg)
                except Exception:
                    print(f"[DeepL INIT ERROR] {error_msg}")
                return False
            return False

        while self.config.running.is_set():
            try:
                en, is_complete, mode = self.config.translation_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            # API 선택 변경 시 재초기화
            if last_api_selected != self.config.selected_api:
                self.translator_deepl = None
                last_api_selected = self.config.selected_api

            # 번역 전 영어 원문 처리
            proc_en = en

            # 단어장 단어 치환 (영어 → 한글)
            replaced_words = []
            with self.config.glossary_lock:
                for eng_word, kor_word in self.config.glossary.items():
                    # 대소문자 구분 없이 단어 단위로 치환
                    pattern = r'\b' + re.escape(eng_word) + r'\b'
                    if re.search(pattern, proc_en, flags=re.IGNORECASE):
                        proc_en = re.sub(pattern, f"【{kor_word}】", proc_en, flags=re.IGNORECASE)
                        replaced_words.append(f"{eng_word}→{kor_word}")

            kr = "[...]"
            retry_count = 3
            last_exc = None
            while retry_count > 0:
                try:
                    if self.config.selected_api == "Azure":
                        url = "https://api.cognitive.microsofttranslator.com/translate?api-version=3.0&to=ko"
                        headers = {
                            'Ocp-Apim-Subscription-Key': self.config.api_key,
                            'Ocp-Apim-Subscription-Region': self.config.azure_region,
                            'Content-type': 'application/json'
                        }
                        resp = requests.post(url, headers=headers, json=[{'text': proc_en}], timeout=2.5)
                        resp.raise_for_status()
                        kr = resp.json()[0]['translations'][0]['text']
                    elif self.config.selected_api == "DeepL":
                        # 매번 번역 시도 전에 Translator가 없으면 초기화
                        if not self.translator_deepl:
                            current_time = time.time()
                            # 초기화 재시도는 2초 간격 (너무 자주 시도하지 않기)
                            if current_time - last_init_time >= 2:
                                init_deepl()
                                last_init_time = current_time

                        if not self.translator_deepl:
                            raise RuntimeError("DeepL Translator가 초기화되지 않았습니다. API 키와 인터넷 연결을 확인하세요.")

                        res = self.translator_deepl.translate_text(proc_en, target_lang="KO")
                        kr = getattr(res, "text", str(res))
                    else:
                        error_msg = f"지원하지 않는 API입니다: '{self.config.selected_api}' (유효한 값: 'DeepL' 또는 'Azure')"
                        print(f"[ERROR] {error_msg}, selected_api 타입: {type(self.config.selected_api)}")
                        raise RuntimeError(error_msg)
                    break
                except Exception as e:
                    last_exc = e
                    retry_count -= 1
                    if retry_count == 0:
                        kr = f"[API ERR] {e}"
                    else:
                        time.sleep(0.5)

            # 번역 후 단어장 치환 부분을 원래 단어장 번역으로 복원
            with self.config.glossary_lock:
                for eng_word, kor_word in self.config.glossary.items():
                    # 【한글】 형태를 찾아서 단어장 번역으로 변경
                    kr = kr.replace(f"【{kor_word}】", kor_word)

            try:
                self.main_win.log_signal.emit("TRANS", "PASS", f"{en} -> {kr}")
            except Exception:
                pass

            # 번역 결과를 UI에 업데이트
            self.update_signal.emit(en, kr, is_complete, mode)

    @QtCore.Slot(str, str, bool, str)
    def update_ui(self, en, kr, is_complete, mode):
        if mode == "UPDATE" and self.text_lines:
            self.text_lines[-1] = [en, kr, is_complete]
        else:
            self.text_lines.append([en, kr, is_complete])
            if len(self.text_lines) > MAX_HISTORY_LIMIT:
                self.text_lines.pop(0)
        if not self.is_edit_mode:
            self.scroll_offset = 0
        self.update()

    @QtCore.Slot(str)
    def enqueue_recognition(self, text):
        self.last_recognition_time = time.time()
        clean = text.strip()
        is_complete = clean.endswith(('.', '?', '!'))
        full = f"{self.pending_buffer} {clean}" if self.pending_buffer else clean
        mode = "UPDATE" if self.pending_buffer else "NEW"
        self.config.translation_queue.put((full, is_complete, mode))
        if is_complete:
            self.pending_buffer = ""
        else:
            self.pending_buffer = full

    @QtCore.Slot(bool)
    def update_vad_status(self, status):
        if self.is_speaking != status:
            self.is_speaking = status
            self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        if self.is_edit_mode:
            p.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 160))
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 0), 2))
            p.drawRect(self.rect().adjusted(1, 1, -1, -1))

            # 우하단에 편집 정보 표시
            info_font = QtGui.QFont("Arial", 10, QtGui.QFont.Weight.Bold)
            p.setFont(info_font)
            info_text = (
                f"폰트: {self.font_size}pt | 최대 줄: {self.max_display_lines}"
                f" | 줄간격: {self.line_spacing}px"
            )
            info_rect = QtCore.QRect(self.width() - 250, self.height() - 30, 240, 25)

            # 텍스트 배경
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(QtGui.QColor(0, 0, 0, 120))
            p.drawRect(info_rect)

            # 텍스트 그리기
            p.setPen(QtGui.QColor(255, 255, 0))
            p.drawText(info_rect, QtCore.Qt.AlignmentFlag.AlignCenter, info_text)

        p.setBrush(QtGui.QColor(255, 0, 0) if self.is_speaking else QtGui.QColor(100, 100, 100))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawEllipse(self.width() - 30, 15, 12, 12)
        p.setFont(self.font)
        fm = p.fontMetrics()
        y = fm.height() + 10
        if len(self.text_lines) <= self.max_display_lines:
            display_data = self.text_lines
        else:
            end_idx = len(self.text_lines) - self.scroll_offset
            start_idx = max(0, end_idx - self.max_display_lines)
            display_data = self.text_lines[start_idx:end_idx]
        for en, kr, is_comp in display_data:
            alpha = 255 if is_comp else 180
            kr_y = y + fm.height() + 2

            # 부드러운 그림자 (블러 효과)
            shadow_offsets = [(2, 2), (2, 1), (1, 2), (1, 1)]
            shadow_color = QtGui.QColor(0, 0, 0, int(alpha * 0.6))
            for dx, dy in shadow_offsets:
                p.setPen(shadow_color)
                p.drawText(15 + dx, y + dy, f"● {en}")
                p.drawText(15 + dx, kr_y + dy, f"ㄴ {kr}")

            # 메인 텍스트
            p.setPen(QtGui.QColor(255, 255, 0, alpha))
            p.drawText(15, y, f"● {en}")
            p.setPen(QtGui.QColor(255, 255, 255, alpha))
            p.drawText(15, kr_y, f"ㄴ {kr}")
            y += (fm.height() * 2) + self.line_spacing
