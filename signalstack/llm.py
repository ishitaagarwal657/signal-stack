"""Gemini free-tier client (REST). Set GEMINI_API_KEY. Model overridable with GEMINI_MODEL."""
import os, json, time, re, threading, requests

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
_lock = threading.Lock()
_last = [0.0]
MIN_GAP = float(os.environ.get("LLM_MIN_GAP", "4.5"))  # free tier is ~15 requests/min


class LLMError(Exception):
    pass


def _parse(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            return json.loads(m.group(1))
        raise


def ask(prompt, system=None, as_json=True, temperature=0.3):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise LLMError("GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature}}
    if as_json:
        body["generationConfig"]["responseMimeType"] = "application/json"
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    last_err = None
    for attempt in range(5):
        with _lock:
            wait = MIN_GAP - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            r = requests.post(url, params={"key": key}, json=body, timeout=90)
        except requests.RequestException as e:
            last_err = str(e)
            print(f"[llm] network error, retry {attempt+1}: {last_err[:100]}", flush=True)
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 503):
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            print(f"[llm] {MODEL} {last_err} - waiting before retry {attempt+1}/5", flush=True)
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise LLMError(f"Gemini error {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            if not text.strip():
                raise ValueError("empty")
        except Exception:
            reason = ""
            try:
                reason = data["candidates"][0].get("finishReason", "") or str(data.get("promptFeedback", ""))
            except Exception:
                pass
            raise LLMError(f"Unexpected Gemini response ({reason}): {r.text[:300]}")
        if not as_json:
            return text
        try:
            return _parse(text)
        except Exception:
            last_err = "bad JSON from model"
            continue
    raise LLMError(f"Gemini failed after retries: {last_err}")
