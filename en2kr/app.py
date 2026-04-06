"""
EN2KR4GAMING - Application entry point
Combines CUDA setup, initialization sequence, and main event loop.
"""

import os
import sys
import json
import time


def main():
    # ──────────────────────────────────────────────────────────
    # 1. CUDA path setup for EXE environment (torch import 이전)
    # ──────────────────────────────────────────────────────────
    if getattr(sys, 'frozen', False):
        cuda_base = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
        if os.path.exists(cuda_base):
            cuda_versions = [d for d in os.listdir(cuda_base)
                             if os.path.isdir(os.path.join(cuda_base, d))]
            cuda_versions.sort(reverse=True)
            for version in cuda_versions:
                cuda_path = os.path.join(cuda_base, version, "bin")
                if os.path.exists(cuda_path):
                    os.environ['PATH'] = cuda_path + os.pathsep + os.environ.get('PATH', '')
                    break

    # ──────────────────────────────────────────────────────────
    # 2. Import heavy dependencies AFTER CUDA setup
    # ──────────────────────────────────────────────────────────
    import torch
    import sounddevice as sd
    from PySide6 import QtWidgets, QtCore
    from faster_whisper import WhisperModel
    import webbrowser

    from en2kr.config import (
        AppConfig, load_settings, save_settings, init_directories,
        log_startup_error,
        SETTINGS_FILE, VAD_MODEL_DIR, VAD_CACHE_DIR,
        WHISPER_MODEL_SIZE, SAMPLE_RATE, ALLOW_VAD_DOWNLOAD,
    )
    from en2kr.dialogs import SetupDialog, ReuseConfirmDialog, SetupWizardDialog
    from en2kr.audio import find_optimal_audio_device, make_audio_callback
    from en2kr.pipeline import vad_worker, stt_worker
    from en2kr.manager import MainWindow
    from en2kr.overlay import OverlayWindow

    # ──────────────────────────────────────────────────────────
    # 3. Directory init
    # ──────────────────────────────────────────────────────────
    init_directories()

    # ──────────────────────────────────────────────────────────
    # 4. Windows console hiding
    # ──────────────────────────────────────────────────────────
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32')
        user32 = ctypes.WinDLL('user32')
        SW_HIDE = 0
        hWnd = kernel32.GetConsoleWindow()
        if hWnd:
            user32.ShowWindow(hWnd, SW_HIDE)

    # ──────────────────────────────────────────────────────────
    # 5. stdout/stderr handling (--noconsole 빌드)
    # ──────────────────────────────────────────────────────────
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')

    # ──────────────────────────────────────────────────────────
    # 6. QApplication creation
    # ──────────────────────────────────────────────────────────
    app = QtWidgets.QApplication(sys.argv)

    # ──────────────────────────────────────────────────────────
    # 7. Config creation and loading
    # ──────────────────────────────────────────────────────────
    config = AppConfig()
    load_settings(config)
    config.running.set()

    # ──────────────────────────────────────────────────────────
    # 8. Init dialog (프로그램 초기화 진행 표시)
    # ──────────────────────────────────────────────────────────
    init_dialog = QtWidgets.QProgressDialog("프로그램을 초기화하는 중입니다...", None, 0, 0)
    init_dialog.setWindowTitle("EN2KR4GAMING")
    init_dialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
    init_dialog.setCancelButton(None)
    init_dialog.setMinimumDuration(0)
    init_dialog.setRange(0, 0)
    init_dialog.setFixedSize(400, 100)
    init_dialog.show()

    for _ in range(10):
        app.processEvents()
    time.sleep(0.2)

    # ──────────────────────────────────────────────────────────
    # 9. API setup loop
    # ──────────────────────────────────────────────────────────
    setup_complete = False
    while not setup_complete:
        use_saved = False

        # 저장된 설정이 있으면 재사용 확인
        if config.selected_api and config.api_key:
            init_dialog.setLabelText("저장된 API 설정을 확인하는 중...")
            for _ in range(5):
                app.processEvents()
            time.sleep(0.1)

            confirm = ReuseConfirmDialog(config.selected_api, config.api_key)
            init_dialog.hide()
            result = confirm.exec()
            if result == QtWidgets.QMessageBox.StandardButton.Yes:
                use_saved = True
                print(f"[SYSTEM] 저장된 설정 사용: {config.selected_api}")
                setup_complete = True
            elif result == QtWidgets.QMessageBox.StandardButton.No:
                init_dialog.show()
                init_dialog.setLabelText("새로운 API 설정을 준비하는 중...")
                for _ in range(5):
                    app.processEvents()
                time.sleep(0.1)
            else:
                # 취소 버튼 - 프로그램 종료
                init_dialog.close()
                sys.exit()

        # 새로운 설정이 필요한 경우
        if not use_saved:
            init_dialog.setLabelText("API 설정 창을 여는 중...")
            for _ in range(5):
                app.processEvents()
            time.sleep(0.1)

            setup = SetupDialog(config)
            init_dialog.hide()
            if setup.exec() == QtWidgets.QDialog.Accepted:
                if config.selected_api and config.api_key and config.selected_api.strip() and config.api_key.strip():
                    print(f"[SYSTEM] 새 API 설정 완료: {config.selected_api}")
                    setup_complete = True
                else:
                    print("[ERROR] 설정이 제대로 저장되지 않았습니다. 다시 시도해주세요.")
                    init_dialog.show()
                    init_dialog.setLabelText("설정 재시도 중...")
                    for _ in range(5):
                        app.processEvents()
                    time.sleep(0.1)
                    QtWidgets.QMessageBox.warning(None, "알림", "설정이 제대로 저장되지 않았습니다. 다시 시도해주세요.")
            else:
                print("[INFO] 사용자가 설정을 취소했습니다.")
                init_dialog.close()
                sys.exit()

    init_dialog.close()

    # 최종 설정 확인 및 로깅
    print(f"[SYSTEM] 최종 API Configuration: '{config.selected_api}' (타입: {type(config.selected_api).__name__})")
    print(f"[SYSTEM] API Key Set: {bool(config.api_key and config.api_key.strip())}")
    print(f"[SYSTEM] Azure Region: {config.azure_region}")

    # ──────────────────────────────────────────────────────────
    # 10. Model loading dialog
    # ──────────────────────────────────────────────────────────
    loading_dialog = QtWidgets.QProgressDialog("AI 모델을 로드 중입니다...", None, 0, 3)
    loading_dialog.setWindowTitle("로딩 중")
    loading_dialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
    loading_dialog.setCancelButton(None)
    loading_dialog.setMinimumDuration(0)
    loading_dialog.setValue(0)
    loading_dialog.show()
    app.processEvents()

    print("[SYSTEM] Loading Models (Whisper/VAD)... Please wait.")

    # ──────────────────────────────────────────────────────────
    # 11. GPU detection
    # ──────────────────────────────────────────────────────────
    config.device = "cpu"
    cuda_available = torch.cuda.is_available()
    cuda_build = getattr(torch.version, "cuda", None)

    if cuda_available:
        config.device = "cuda"
        print(f"[GPU] CUDA (NVIDIA) 감지됨: {torch.version.cuda}")
    else:
        try:
            import torch_directml
            config.device = torch_directml.device()
            print("[GPU] DirectML (Windows GPU) 활성화됨")
        except ImportError:
            try:
                import intel_extension_for_pytorch  # noqa: F401
                config.device = "xpu"
                print("[GPU] Intel Extension 활성화됨")
            except ImportError:
                print("[GPU] GPU 감지 안 됨 - CPU 모드로 실행합니다")

    # CPU 모드 경고
    if config.device == "cpu":
        loading_dialog.hide()

        msg_box = QtWidgets.QMessageBox()
        msg_box.setWindowTitle("⚠️ GPU 가속 미사용 경고")
        msg_box.setIcon(QtWidgets.QMessageBox.Warning)
        msg_box.setText(
            "<b>⚠️ GPU 가속을 사용할 수 없습니다.</b><br><br>"
            "<span style='color: #ff8800; font-weight: bold;'>CPU 모드로 실행됩니다. 번역 속도가 매우 느려질 수 있습니다.</span><br><br>"
            "<b>GPU 가속을 원하시나요?</b>"
        )

        gpu_options = ""
        if not cuda_build:
            gpu_options += "• <b>NVIDIA CUDA</b>: <u>pip install --upgrade torch --index-url https://download.pytorch.org/whl/cu124</u><br>"
        if not cuda_available:
            gpu_options += "• <b>Windows GPU (DirectML)</b>: <u>pip install torch-directml</u><br>"
            gpu_options += "• <b>Intel Arc GPU</b>: <u>pip install intel-extension-for-pytorch</u><br>"

        msg_box.setInformativeText(
            "📊 <b>성능 비교:</b><br>"
            "• GPU 가속: <span style='color: #00ff00; font-weight: bold;'>실시간 번역 (1~2초)</span><br>"
            "• CPU 모드: <span style='color: #ff0000; font-weight: bold;'>느린 번역 (5~10초)</span><br><br>"
            "<b>GPU 옵션 (설치 명령어):</b><br>" + gpu_options + "<br>"
            "'예'를 선택하면 PyTorch GPU 설치 페이지로 이동합니다.<br>"
            "'아니요'를 선택하면 느린 CPU 모드로 계속 진행합니다."
        )
        msg_box.setStandardButtons(
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        msg_box.setDefaultButton(QtWidgets.QMessageBox.No)

        # "다시 보지 않기" 체크박스
        dont_show_again = QtWidgets.QCheckBox("다시 보지 않기")
        msg_box.setCheckBox(dont_show_again)

        # 설정 파일에서 무시 여부 확인
        show_warning = True
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                    if settings.get("ignore_gpu_warning", False):
                        show_warning = False
        except Exception:
            pass

        if show_warning:
            result = msg_box.exec()

            # 체크박스 상태 저장
            if dont_show_again.isChecked():
                try:
                    settings = {}
                    if os.path.exists(SETTINGS_FILE):
                        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                            settings = json.load(f)
                    settings["ignore_gpu_warning"] = True
                    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                        json.dump(settings, f, indent=4, ensure_ascii=False)
                except Exception:
                    pass

            if result == QtWidgets.QMessageBox.Yes:
                webbrowser.open("https://pytorch.org/get-started/locally/")
                QtWidgets.QMessageBox.information(
                    None,
                    "GPU 설치 안내",
                    "PyTorch GPU 설치 페이지가 열렸습니다.\n\n"
                    "설치 완료 후 프로그램을 재시작하면\n"
                    "자동으로 GPU 가속이 활성화됩니다.\n\n"
                    "현재는 CPU 모드로 계속 진행합니다."
                )

        loading_dialog.show()

    # ──────────────────────────────────────────────────────────
    # 12. Whisper loading
    # ──────────────────────────────────────────────────────────
    device_str = config.device if isinstance(config.device, str) else "directml"
    loading_dialog.setLabelText(f"Whisper 모델 로드 중... (Device: {device_str.upper()})")
    loading_dialog.setValue(1)
    app.processEvents()
    try:
        config.whisper_model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=config.device,
            compute_type="float16" if config.device == "cuda" else "int8"
        )
    except Exception as e:
        log_startup_error(
            "Whisper 모델 로드 실패",
            "Whisper 모델을 초기화하지 못했습니다.",
            str(e)
        )
        sys.exit(1)

    # ──────────────────────────────────────────────────────────
    # 13. VAD loading (3-stage)
    # ──────────────────────────────────────────────────────────
    loading_dialog.setLabelText("VAD 모델 로드 중...")
    loading_dialog.setValue(2)
    app.processEvents()

    config.vad_model = None
    vad_load_error = None

    try:
        # 시도 1: 프로그램 폴더 내 모델 직접 로드 (.jit 파일)
        vad_jit_path = os.path.join(VAD_MODEL_DIR, "silero_vad.jit")
        if os.path.exists(vad_jit_path):
            try:
                config.vad_model = torch.jit.load(vad_jit_path)
                config.vad_model.eval()
                print(f"[VAD] 프로그램 내장 모델 로드 성공: {vad_jit_path}")
            except Exception as e:
                print(f"[VAD] 내장 모델 로드 실패: {e}")
                vad_load_error = e

        # 시도 2: torch.hub를 통한 로드 (폴백)
        if config.vad_model is None:
            torch.hub.set_dir(VAD_CACHE_DIR)

            # 2-1: 로컬 캐시에서 로드
            try:
                config.vad_model, _ = torch.hub.load(
                    repo_or_dir='snakers4/silero-vad',
                    model='silero_vad',
                    trust_repo=True,
                    force_reload=False,
                    skip_validation=True
                )
                print("[VAD] 로컬 캐시에서 모델 로드 성공")
            except Exception as e1:
                print(f"[VAD] 로컬 캐시 로드 실패: {e1}")

                # 2-2: 사용자가 수동 다운로드한 폴더에서 로드
                local_vad_path = os.path.join(VAD_CACHE_DIR, 'snakers4_silero-vad_master')
                if os.path.exists(local_vad_path):
                    try:
                        config.vad_model, _ = torch.hub.load(
                            repo_or_dir=local_vad_path,
                            model='silero_vad',
                            source='local',
                            trust_repo=True
                        )
                        print(f"[VAD] 로컬 디렉토리에서 모델 로드 성공: {local_vad_path}")
                    except Exception as e2:
                        print(f"[VAD] 로컬 디렉토리 로드 실패: {e2}")
                        vad_load_error = e2
                else:
                    # 2-3: 인터넷을 통한 다운로드 (옵션)
                    if ALLOW_VAD_DOWNLOAD:
                        try:
                            config.vad_model, _ = torch.hub.load(
                                repo_or_dir='snakers4/silero-vad',
                                model='silero_vad',
                                trust_repo=True,
                                force_reload=True
                            )
                            print("[VAD] 인터넷을 통한 모델 다운로드 성공")

                            # 다운로드 성공 시 .jit 파일로 저장
                            try:
                                torch.jit.save(torch.jit.script(config.vad_model), vad_jit_path)
                                print(f"[VAD] 모델을 프로그램 폴더에 저장: {vad_jit_path}")
                            except Exception:
                                pass
                        except Exception as e3:
                            print(f"[VAD] 인터넷 다운로드 실패: {e3}")
                            vad_load_error = e3
                    else:
                        vad_load_error = RuntimeError("VAD 자동 다운로드가 비활성화되어 있습니다.")

        if config.vad_model is None:
            raise RuntimeError(f"VAD 모델 로드 실패: {vad_load_error}")

    except Exception as e:
        error_msg = str(e)
        help_text = (
            "⚠️ VAD 모델을 로드할 수 없습니다.\n\n"
            "해결 방법:\n\n"
            "1️⃣ 모델 파일 직접 다운로드 (권장):\n"
            "   - https://github.com/snakers4/silero-vad/blob/master/files/silero_vad.jit\n"
            "   - 'Download' 버튼으로 silero_vad.jit 파일 다운로드\n"
            f"   - 다운받은 파일을 다음 경로에 복사:\n"
            f"     {VAD_MODEL_DIR}\\silero_vad.jit\n\n"
            "2️⃣ 전체 저장소 다운로드:\n"
            "   - https://github.com/snakers4/silero-vad 접속\n"
            "   - 'Code' -> 'Download ZIP' 클릭\n"
            f"   - 압축 해제 후 폴더를 다음 경로에 복사:\n"
            f"     {VAD_CACHE_DIR}\\snakers4_silero-vad_master\\\n\n"
            "3️⃣ 네트워크 확인:\n"
            "   - 프록시/방화벽/VPN 설정 확인\n\n"
            f"오류 상세: {error_msg}"
        )
        QtWidgets.QMessageBox.critical(None, "VAD 모델 로드 실패", help_text)
        sys.exit(1)

    loading_dialog.setValue(3)
    loading_dialog.close()

    # ──────────────────────────────────────────────────────────
    # 14. Create windows
    # ──────────────────────────────────────────────────────────
    mw = MainWindow(config)
    mw.show()
    ov = OverlayWindow(config, mw)
    mw.overlay = ov

    # ──────────────────────────────────────────────────────────
    # 15. Hotkeys
    # ──────────────────────────────────────────────────────────
    import keyboard
    keyboard.add_hotkey('f12', ov.toggle_edit_mode)
    keyboard.add_hotkey('=', lambda: ov.change_font_size(2))
    keyboard.add_hotkey('-', lambda: ov.change_font_size(-2))

    # ──────────────────────────────────────────────────────────
    # 16. Worker threads
    # ──────────────────────────────────────────────────────────
    import threading
    threading.Thread(target=vad_worker, args=(config, mw, ov), daemon=True).start()
    threading.Thread(target=stt_worker, args=(config, mw, ov), daemon=True).start()

    # ──────────────────────────────────────────────────────────
    # 17. Audio device + stream
    # ──────────────────────────────────────────────────────────
    idx = find_optimal_audio_device()
    if idx is None:
        print("[ERROR] 적합한 오디오 장치를 찾을 수 없습니다.")
        QtWidgets.QMessageBox.warning(
            None,
            "오디오 장치 미감지",
            "VB-Audio Virtual Cable이 감지되지 않았습니다.\n초기 설정 마법사를 시작합니다."
        )
        wizard = SetupWizardDialog()
        result = wizard.exec()
        if result != QtWidgets.QDialog.Accepted:
            sys.exit(1)
        idx = find_optimal_audio_device()
        if idx is None:
            QtWidgets.QMessageBox.critical(
                None,
                "설정 실패",
                "여전히 오디오 장치를 찾을 수 없습니다.\nVB Cable 설치 후 프로그램을 재시작해주세요."
            )
            sys.exit(1)

    try:
        stream = sd.InputStream(
            device=idx, channels=1, samplerate=SAMPLE_RATE,
            callback=make_audio_callback(config)
        )
        stream.start()
        exit_code = app.exec()
    except Exception as e:
        log_startup_error(
            "오디오 스트림 오류",
            "오디오 입력 스트림을 시작하지 못했습니다.\n장치 설정을 확인해주세요.",
            str(e)
        )
        exit_code = 1
    finally:
        config.running.clear()
        if 'stream' in locals():
            stream.stop()
            stream.close()
        sys.exit(exit_code)
