import time
import queue
import numpy as np
import torch
from datetime import datetime, timedelta
from en2kr.config import AppConfig, VAD_WINDOW_SIZE


class HallucinationTracker:
    def __init__(self, config: AppConfig, main_win):
        self.config = config
        self.main_win = main_win
        self.history = {}

    def check_and_ban(self, text: str) -> bool:
        clean = text.strip()
        if not clean:
            return False
        with self.config.filter_lock:
            for banned in self.config.hallucination_filter:
                if banned.lower() == clean.lower():
                    return True
        if len(clean.split()) > 3:
            return False
        now = datetime.now()
        with self.config.filter_lock:
            if clean not in self.history:
                self.history[clean] = []
            self.history[clean].append(now)
            self.history[clean] = [t for t in self.history[clean] if now - t < timedelta(minutes=10)]
            if len(self.history[clean]) >= 5:
                if clean not in self.config.hallucination_filter:
                    self.config.hallucination_filter.append(clean)
                    self.main_win.update_list_signal.emit()
                    return True
        return False


def vad_worker(config: AppConfig, main_win, overlay):
    vad_acc = np.array([], dtype=np.float32); speech_buf = []; in_speech = False; last_speech = time.time(); preroll = []
    while config.running.is_set():
        try: chunk = config.audio_queue.get(timeout=0.1)
        except queue.Empty: continue
        preroll.append(chunk)
        if len(preroll) > 22: preroll.pop(0)
        vad_acc = np.concatenate([vad_acc, chunk])
        while len(vad_acc) >= VAD_WINDOW_SIZE:
            current = vad_acc[:VAD_WINDOW_SIZE]; vad_acc = vad_acc[VAD_WINDOW_SIZE:]
            prob = config.vad_model(torch.from_numpy(current).float().unsqueeze(0), 16000).item()
            if prob > config.vad_threshold:
                last_speech = time.time()
                if not in_speech: in_speech = True; speech_buf = list(preroll); main_win.vad_ui_signal.emit(True); overlay.vad_status_signal.emit(True)
            if in_speech:
                speech_buf.append(current)
                if (time.time() - last_speech) > config.silence_duration:
                    in_speech = False; main_win.vad_ui_signal.emit(False); overlay.vad_status_signal.emit(False)
                    if speech_buf: config.inference_queue.put(np.concatenate(speech_buf)); speech_buf = []


def stt_worker(config: AppConfig, main_win, overlay):
    last_end = time.time()
    last_text = ""  # 마지막으로 처리한 텍스트 저장

    while config.running.is_set():
        try: audio_data = config.inference_queue.get(timeout=0.1)
        except queue.Empty: continue
        audio_data = audio_data - np.mean(audio_data); rms = np.sqrt(np.mean(audio_data**2))
        if rms > 1e-6: audio_data *= (0.1 / (rms + 1e-9))
        cond = (time.time() - last_end) < config.context_reset_seconds
        segments, _ = config.whisper_model.transcribe(audio_data, language="en", beam_size=5, condition_on_previous_text=cond)

        # 모든 segment를 하나로 합쳐서 전송 (중복 방지)
        valid_segments = []
        for s in segments:
            txt = s.text.strip()
            if s.no_speech_prob < config.max_no_speech_prob and s.avg_logprob > config.min_avg_logprob:
                if main_win.tracker.check_and_ban(txt):
                    main_win.log_signal.emit("STT", "BLOCK", f"차단됨: {txt}")
                else:
                    valid_segments.append(txt)

        # 유효한 segment들을 하나로 합쳐서 한 번만 emit
        if valid_segments:
            combined_text = " ".join(valid_segments)

            # 이전 텍스트와 중복 제거
            if last_text:
                # 마지막 텍스트의 단어들을 가져옴
                last_words = last_text.lower().split()
                current_words = combined_text.split()

                # 현재 텍스트의 시작 부분이 이전 텍스트와 겹치는지 확인
                overlap_count = 0
                for i in range(min(5, len(last_words), len(current_words))):  # 최대 5단어까지만 체크
                    if current_words[i].lower() in last_words[-5:]:  # 마지막 5단어 중에 있으면
                        overlap_count = i + 1
                    else:
                        break

                # 겹치는 부분이 있으면 제거
                if overlap_count > 0:
                    combined_text = " ".join(current_words[overlap_count:])
                    if not combined_text.strip():  # 중복 제거 후 내용이 없으면 skip
                        continue

            overlay.recognition_signal.emit(combined_text)
            last_text = combined_text
            last_end = time.time()
            main_win.log_signal.emit("STT", "PASS", combined_text)
