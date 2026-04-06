# EN2KR4GAMING 코드 심층 분석 (2026-04-07)

모듈 분리 이전 단일 파일(1,708줄) 기준 시니어 엔지니어 관점 분석.
모듈 분리 완료 후에도 유효한 항목은 **[미해결]** 표시.

## 1. 아키텍처

| 항목 | 상태 | 비고 |
|------|------|------|
| God File 패턴 | **해결** | en2kr/ 패키지 8개 모듈로 분리 |
| 전역 상태 남용 | **해결** | AppConfig dataclass로 통합 |
| 관심사 미분리 | **해결** | 역할별 모듈 분리 완료 |

## 2. 동시성

| 위치 | 문제 | 상태 |
|------|------|------|
| vad_worker | `vad_threshold` 락 없이 읽기 | **[미해결]** — GIL이 보호하지만 원리적 ��험 |
| stt_worker | `context_reset_seconds` ��� 없이 읽기 | **[미해결]** — 동일 |
| translation_worker | `selected_api`, `api_key` 락 없이 읽기 | **[미해결]** — 동일 |
| audio_callback | 큐 오버플로우 시 데이터 드롭 | 허용 — 실시간 시스템에서 의도된 동작 |
| `running_flag` bool | 스레드 종료 대기 ��가 | **해결** — `threading.Event`로 변경 |

> Python GIL 특성상 float/str 단일 속성의 torn read는 실제 ��생하지 않음. P2 이하.

## 3. 설��� 관리

| 항목 | 상태 | 비고 |
|------|------|------|
| save_settings 데이터 유실 | **해결** | 기존 파일 로드 후 병합 방식으로 변경 |
| 기본 용어집 중복 정의 | **해결** | `DEFAULT_GLOSSARY` 한 곳에만 |
| bare `except: pass` | **해결** | `except Exception as e: print(...)` |
| overlay geometry 직접 JSON 읽기 | **[미해결]** | overlay.py에서 SETTINGS_FILE 직접 접근 |
| GPU 경고 설정 직접 JSON 읽기 | **[미해결]** | app.py에�� ignore_gpu_warning 직접 접근 |

## 4. 번역 파이프라인

### 잘 설계된 부분
- 용어집 `【】` 마커 보호 방�� — 번역 API가 고유명사 훼손 방지
- 환각 자동 감지 (10분 내 5회 반복 → 자동 차단)
- Whisper `condition_on_previous_text` 활용 — 문맥 연속성

### 미해결 개선 사항

| 항목 | 설명 | 우선순위 |
|------|------|----------|
| 번역 재시도 | 3회 × `sleep(0.5)` = 최대 1.5초 블로킹 | P2 — exponential backoff 권장 |
| STT 중��� 제거 | 단어 단위 overlap 비교 불안정 (`in` 비교로 false positive) | P2 |
| `speech_buf` 크기 제한 없음 | 극단적으��� 긴 발화 시 메모리 급증 | P3 |

## 5. UI/UX

### 잘 된 점
- 클릭스루 오버레이 (`WS_EX_TRANSPARENT`)
- F12 편집모드 토글
- 텍스트 4방향 그림자 — 가독성
- VB-Cable 설��� 마법사

### 미해결 사항

| 항목 | 설명 | 우선순위 |
|------|------|----------|
| 글로벌 핫키 충돌 | `keyboard` 라이브러리의 `-`/`=` 키가 게임과 충돌 가능 | P2 |
| paintEvent 매 프레임 전체 렌더링 | 텍스트 정적일 때도 동일 비용 | P3 |

## 6. 리소스 관리

| 항목 | 상태 |
|------|------|
| audio_queue 6MB 상시 할당 | 적절 |
| text_lines 100개 제한 | 적절 |
| speech_buf 크기 무제한 | **[미해결]** P3 |
| closeEvent ���레드 종료 대기 없음 | **[미해결]** P3 — daemon 스레드라 프로세스 종료 시 자동 정리 |

## 7. 보안
- API 키 base64 인코딩 — 암호화 아님, 단순 난독화. 로컬 앱이라 실질 위협 낮음.

## 8. 종합 등급 (모듈 분리 후)

| 영역 | 이전 | 이후 | 비고 |
|------|------|------|------|
| 기능 완성도 | B+ | B+ | 변경 없음 |
| 코드 구조 | D | **B+** | 패키지 분리, AppConfig 통합 |
| 동시성 안전 | C- | **C+** | Event 도입, 나머지는 GIL 의존 |
| 오류 처리 | D+ | **B-** | bare except 제거, 로깅 추가 |
| 설정 관리 | C | **B** | 데이터 유실 방지, 중�� 제거 |
| 사용자 경험 | B+ | B+ | 변경 없음 |
| 확장성 | D | **B** | 새 모듈/API 추가 용이 |

## 9. 향후 개선 우선순위

| 순위 | 항목 | 설명 |
|------|------|------|
| P1 | 오버레이 설정 AppConfig 통합 | geometry/font 설정을 config에 포함하여 직접 JSON 접근 제거 |
| P2 | 번역 재시도 개선 | exponential backoff, 타임아웃 설정 가능하게 |
| P2 | 글로벌 핫키 충돌 방지 | 편집모드 진입 시에만 활성화하거나 modifier key 조합 |
| P2 | STT 중복 제거 로직 안정화 | 단어 순서 고려한 비교로 변경 |
| P3 | speech_buf 크기 상한 | 최대 30초 분량 제한 |
| P3 | paintEvent ��적화 | 텍스트 변경 시에만 재렌더링 |
