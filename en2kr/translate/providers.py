"""Translation API adapters. All translate into Korean.

Errors are normalised so the service can decide whether to fall back:
AuthError (bad key / API not enabled), QuotaError (free tier used up) and
TransientError (network, rate limit, server error).
"""
import html
import logging

import requests

log = logging.getLogger(__name__)

TIMEOUT = (3.05, 6)


class ProviderError(Exception):
    pass


class AuthError(ProviderError):
    pass


class QuotaError(ProviderError):
    pass


class TransientError(ProviderError):
    pass


class Provider:
    id = ""
    label = ""
    supports_usage = False

    def __init__(self, cfg):
        self.cfg = dict(cfg or {})

    @property
    def configured(self):
        return bool(self.cfg.get("key", "").strip())

    def translate(self, text, source_lang=None):
        raise NotImplementedError

    def usage(self):
        """(used, limit) characters for the current period, or None."""
        return None


def _http_error(label, resp):
    try:
        detail = resp.json()
    except ValueError:
        detail = resp.text[:200]
    return f"{label} HTTP {resp.status_code}: {detail}"


class DeepLProvider(Provider):
    id = "deepl"
    label = "DeepL"
    supports_usage = True
    _SOURCES = {"en", "de", "fr", "es", "ru", "pl", "pt", "it", "nl", "sv", "uk", "tr", "ja", "zh"}

    def __init__(self, cfg):
        super().__init__(cfg)
        self._translator = None

    def _client(self):
        if self._translator is None:
            import deepl
            deepl.http_client.max_network_retries = 1
            self._translator = deepl.Translator(self.cfg["key"].strip(), send_platform_info=False)
        return self._translator

    def _call(self, fn):
        import deepl
        try:
            return fn(self._client())
        except deepl.AuthorizationException as e:
            raise AuthError(f"DeepL 키가 올바르지 않습니다 ({e})") from e
        except deepl.QuotaExceededException as e:
            raise QuotaError("DeepL 월 무료 한도를 모두 사용했습니다") from e
        except (deepl.TooManyRequestsException, deepl.ConnectionException) as e:
            raise TransientError(f"DeepL 일시 오류: {e}") from e
        except deepl.DeepLException as e:
            raise ProviderError(f"DeepL 오류: {e}") from e

    def translate(self, text, source_lang=None):
        src = source_lang.upper() if source_lang in self._SOURCES else None
        res = self._call(lambda t: t.translate_text(text, target_lang="KO", source_lang=src))
        return res.text

    def usage(self):
        u = self._call(lambda t: t.get_usage())
        if u.character.valid:
            return u.character.count, u.character.limit
        return None


class AzureProvider(Provider):
    id = "azure"
    label = "Azure Translator"
    URL = "https://api.cognitive.microsofttranslator.com/translate"

    def translate(self, text, source_lang=None):
        params = {"api-version": "3.0", "to": "ko"}
        if source_lang:
            params["from"] = source_lang
        headers = {"Ocp-Apim-Subscription-Key": self.cfg["key"].strip(), "Content-type": "application/json"}
        region = (self.cfg.get("region") or "").strip()
        if region:
            headers["Ocp-Apim-Subscription-Region"] = region
        try:
            resp = requests.post(self.URL, params=params, headers=headers, json=[{"text": text}], timeout=TIMEOUT)
        except requests.RequestException as e:
            raise TransientError(f"Azure 연결 실패: {e}") from e
        if resp.status_code == 200:
            return resp.json()[0]["translations"][0]["text"]
        code = 0
        try:
            code = int(resp.json().get("error", {}).get("code", 0))
        except (ValueError, AttributeError):
            pass
        if code == 403001:
            raise QuotaError("Azure 무료 한도를 모두 사용했습니다")
        if resp.status_code in (401, 403):
            raise AuthError(f"Azure 키 또는 지역(region)이 올바르지 않습니다 ({_http_error('Azure', resp)})")
        if resp.status_code == 429 or resp.status_code >= 500:
            raise TransientError(_http_error("Azure", resp))
        raise ProviderError(_http_error("Azure", resp))


class GoogleProvider(Provider):
    id = "google"
    label = "Google Cloud Translation"
    URL = "https://translation.googleapis.com/language/translate/v2"

    def translate(self, text, source_lang=None):
        body = {"q": text, "target": "ko", "format": "text"}
        if source_lang:
            body["source"] = source_lang
        try:
            resp = requests.post(self.URL, params={"key": self.cfg["key"].strip()}, json=body, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise TransientError(f"Google 연결 실패: {e}") from e
        if resp.status_code == 200:
            return html.unescape(resp.json()["data"]["translations"][0]["translatedText"])
        reason, message = "", ""
        try:
            err = resp.json().get("error", {})
            message = err.get("message", "")
            reason = (err.get("errors") or [{}])[0].get("reason", "")
        except (ValueError, AttributeError):
            pass
        if reason in ("dailyLimitExceeded", "quotaExceeded"):
            raise QuotaError("Google 번역 할당량을 모두 사용했습니다")
        if resp.status_code == 429 or reason in ("rateLimitExceeded", "userRateLimitExceeded") or resp.status_code >= 500:
            raise TransientError(_http_error("Google", resp))
        if resp.status_code in (400, 401, 403):
            raise AuthError(f"Google API 키 또는 API 사용 설정을 확인하세요 ({message or resp.status_code})")
        raise ProviderError(_http_error("Google", resp))


SYSTEM_PROMPT = (
    "You translate voice chat from an online shooter game into Korean, in real time. "
    "Reply with only the natural, casual Korean translation, as gamers talk - no notes, no quotes. "
    "Text inside 【】 is already Korean: keep it exactly, brackets included."
)


class OpenAICompatProvider(Provider):
    """Any OpenAI-compatible chat API: Gemini, Groq, OpenRouter, Ollama, ..."""
    id = "openai"
    label = "LLM (OpenAI 호환)"

    @property
    def configured(self):
        return bool(self.cfg.get("base_url", "").strip() and self.cfg.get("model", "").strip())

    def _headers(self):
        key = (self.cfg.get("key") or "").strip()
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _url(self, path):
        return self.cfg["base_url"].strip().rstrip("/") + path

    def translate(self, text, source_lang=None):
        body = {
            "model": self.cfg["model"].strip(),
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
            "temperature": 0.2,
            "max_tokens": 512,
            "stream": False,
        }
        try:
            resp = requests.post(self._url("/chat/completions"), headers=self._headers(), json=body, timeout=(3.05, 15))
        except requests.RequestException as e:
            raise TransientError(f"LLM 연결 실패: {e}") from e
        if resp.status_code == 200:
            try:
                out = resp.json()["choices"][0]["message"]["content"] or ""
            except (ValueError, KeyError, IndexError) as e:
                raise ProviderError(f"LLM 응답 형식 오류: {resp.text[:200]}") from e
            return out.strip().strip("`").strip().strip('"').strip()
        # Gemini answers an invalid key with 400 INVALID_ARGUMENT instead of 401.
        if resp.status_code in (401, 403) or (resp.status_code == 400 and "api key" in resp.text.lower()):
            raise AuthError(f"LLM API 키를 확인하세요 ({_http_error('LLM', resp)})")
        if resp.status_code == 429:
            text_l = resp.text.lower()
            if "per day" in text_l or "daily" in text_l:
                raise QuotaError("LLM 일일 무료 한도를 모두 사용했습니다")
            raise TransientError(_http_error("LLM", resp))
        if resp.status_code >= 500:
            raise TransientError(_http_error("LLM", resp))
        raise ProviderError(_http_error("LLM", resp))

    def list_models(self):
        resp = requests.get(self._url("/models"), headers=self._headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        return sorted(m["id"] for m in resp.json().get("data", []))


PROVIDERS = {p.id: p for p in (DeepLProvider, AzureProvider, GoogleProvider, OpenAICompatProvider)}


def create(provider_id, cfg):
    return PROVIDERS[provider_id](cfg)
