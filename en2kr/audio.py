"""
EN2KR4GAMING - Audio module
Audio device selection and callback factory.
"""

import queue
from typing import Callable

import numpy as np
import sounddevice as sd

from en2kr.config import AppConfig, TARGET_KEYWORD


def find_optimal_audio_device(target_keyword: str = TARGET_KEYWORD) -> int | None:
    """
    최적화된 VB 케이블 오디오 장치 선택 (안정성 + 대역폭 우선)
    우선순위:
    1. CABLE Output + DirectSound (16ch) - 안정성 최고 + 전체 대역폭
    2. CABLE Output + MME (16ch) - 호환성 높음
    3. CABLE Output + WASAPI - 품질 우수 (채널 제한 있을 수 있음)
    4. 기타 VB-Audio 입력 장치
    """
    devices = sd.query_devices()
    apis = sd.query_hostapis()

    if isinstance(devices, dict):
        return None

    def get_device_priority(device_name, channels, hostapi_idx):
        """
        VB 케이블 장치의 우선순위 계산 (hostapi 기반)
        """
        name_lower = device_name.lower()
        is_cable_output = "cable output" in name_lower
        api_name = apis[hostapi_idx]['name'].lower()

        # 1순위: DirectSound + 16ch (최고 안정성)
        if is_cable_output and "directsound" in api_name and channels == 16:
            return 0

        # 2순위: MME + 16ch (호환성 높음)
        if is_cable_output and "mme" in api_name and channels == 16:
            return 5

        # 3순위: WASAPI (품질 우수)
        if is_cable_output and "wasapi" in api_name:
            return 10 + (16 - min(channels, 16))

        # 4순위: DirectSound (16ch 미만)
        if is_cable_output and "directsound" in api_name and channels < 16:
            return 20

        # 5순위: MME (16ch 미만)
        if is_cable_output and "mme" in api_name and channels < 16:
            return 25

        # 6순위: WDM-KS (16ch)
        if is_cable_output and "wdm" in api_name and channels >= 16:
            return 30

        # 기타
        return 100

    # 입력 채널이 있는 VB-Audio/CABLE Output 장치만 후보 수집
    candidates = []
    for i in range(len(devices)):
        if devices[i]['max_input_channels'] > 0:
            name_lower = devices[i]['name'].lower()
            if (target_keyword.lower() not in name_lower
                    and "vb-audio" not in name_lower
                    and "virtual cable" not in name_lower
                    and "cable output" not in name_lower):
                continue
            priority = get_device_priority(devices[i]['name'], devices[i]['max_input_channels'], devices[i]['hostapi'])
            candidates.append((i, priority, devices[i]['name'], devices[i]['max_input_channels'], devices[i]['hostapi']))

    # 우선순위별로 정렬
    candidates.sort(key=lambda x: x[1])

    if candidates:
        selected, priority, device_name, channels, hostapi_idx = candidates[0]
        api = apis[hostapi_idx]['name']
        cable_type = "Virtual Cable" if "virtual cable" in device_name.lower() else ("Point" if "point" in device_name.lower() else "Cable")
        print(f"[AUDIO] 최적 장치 선택: 장치#{selected} - {device_name} ({channels}ch, {cable_type}, {api})")
        return selected

    print("[ERROR] 입력 가능한 오디오 장치를 찾을 수 없습니다.")
    return None


def make_audio_callback(config: AppConfig) -> Callable:
    """
    오디오 콜백 클로저 생성.
    Returns a sounddevice-compatible callback that enqueues audio frames
    into config.audio_queue.
    """
    audio_queue = config.audio_queue

    def audio_callback(indata, frames, time_info, status):
        try:
            if audio_queue.qsize() > 1000:
                try:
                    audio_queue.get_nowait()
                except queue.Empty:
                    pass
            audio_queue.put_nowait(indata.copy().flatten())
        except queue.Full:
            pass

    return audio_callback
