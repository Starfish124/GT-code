"""Unified client for local model servers.

Ollama gets its NATIVE /api/chat endpoint: that unlocks keep_alive (models
stay hot in RAM between requests instead of being evicted), think=false
(Qwen3's hidden reasoning mode off by default — the single biggest latency
win), and precise per-request metrics (model load time, prefill time,
generation tok/s) that GT surfaces to the user.

Everything else (LM Studio, vLLM, ...) uses the OpenAI-compatible shape.
"""

import json
import time
import requests


class LLMError(Exception):
    """Friendly, user-facing error (bad connection, model not loaded, etc.)."""


class LLM:
    def __init__(self, config):
        self.config = config
        self.last_metrics = None  # timing of the most recent chat call

    def _perf(self, key, default):
        return getattr(self.config, "performance", {}).get(key, default)

    # ---- chat ---------------------------------------------------------------

    def chat(self, role, messages, stream=True, temperature=0.3,
             on_token=None, timeout=600):
        """Return the assistant's full text. If stream=True, on_token(str) is
        called with each incremental chunk as it arrives. Timing details of
        the call land in self.last_metrics."""
        spec = self.config.model_for(role)
        self.last_metrics = None
        try:
            if spec["provider"] == "ollama":
                return self._chat_ollama(spec, messages, stream, temperature,
                                         on_token, timeout)
            return self._chat_openai(spec, messages, stream, temperature,
                                     on_token, timeout)
        except requests.exceptions.ConnectionError:
            raise LLMError(self._conn_msg(spec))
        except requests.exceptions.Timeout:
            raise LLMError(f"'{spec['model']}' ({spec['provider']}) timed out "
                           f"after {timeout}s.")

    # ---- Ollama native path ---------------------------------------------------

    def _chat_ollama(self, spec, messages, stream, temperature, on_token, timeout):
        base = spec["base_url"]
        base = base[:-3] if base.endswith("/v1") else base
        url = base + "/api/chat"

        think = bool(self._perf("thinking", False))
        model = spec["model"]
        if not think and "qwen3" in model.lower():
            # belt & braces: Qwen3's documented soft switch, for older Ollamas
            # that ignore the think parameter.
            messages = [dict(m) for m in messages]
            for m in reversed(messages):
                if m["role"] == "user":
                    m["content"] = m["content"] + " /no_think"
                    break

        payload = {
            "model": model,
            "messages": messages,
            "stream": bool(stream),
            "think": think,
            "keep_alive": self._perf("keep_alive", "30m"),
            "options": {
                "temperature": temperature,
                "num_ctx": int(self._perf("num_ctx", 8192)),
            },
        }

        if not stream:
            r = requests.post(url, json=payload, timeout=timeout)
            self._raise_for_status(r, spec)
            obj = r.json()
            self.last_metrics = self._ollama_metrics(obj)
            return (obj.get("message") or {}).get("content", "") or ""

        chunks = []
        with requests.post(url, json=payload, stream=True, timeout=timeout) as r:
            self._raise_for_status(r, spec)
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("error"):
                    raise LLMError(f"{model}: {obj['error']}")
                tok = (obj.get("message") or {}).get("content", "")
                if tok:
                    chunks.append(tok)
                    if on_token:
                        on_token(tok)
                if obj.get("done"):
                    self.last_metrics = self._ollama_metrics(obj)
                    break
        return "".join(chunks)

    @staticmethod
    def _ollama_metrics(obj):
        ns = 1e9
        eval_n = obj.get("eval_count") or 0
        eval_s = (obj.get("eval_duration") or 0) / ns
        return {
            "load_s": (obj.get("load_duration") or 0) / ns,
            "prefill_s": (obj.get("prompt_eval_duration") or 0) / ns,
            "prompt_tokens": obj.get("prompt_eval_count") or 0,
            "tokens": eval_n,
            "tps": (eval_n / eval_s) if eval_s > 0 else 0,
            "total_s": (obj.get("total_duration") or 0) / ns,
        }

    # ---- OpenAI-compatible path (LM Studio, vLLM, ...) -------------------------

    def _chat_openai(self, spec, messages, stream, temperature, on_token, timeout):
        url = spec["base_url"] + "/chat/completions"
        payload = {
            "model": spec["model"],
            "messages": messages,
            "temperature": temperature,
            "stream": bool(stream),
        }
        t0 = time.perf_counter()
        if stream:
            text = self._chat_stream(url, payload, on_token, timeout)
        else:
            r = requests.post(url, json=payload, timeout=timeout)
            self._raise_for_status(r, spec)
            text = r.json()["choices"][0]["message"]["content"] or ""
        total = time.perf_counter() - t0
        approx_tokens = max(1, len(text) // 4)
        self.last_metrics = {"load_s": 0, "prefill_s": 0, "prompt_tokens": 0,
                             "tokens": approx_tokens,
                             "tps": approx_tokens / total if total > 0 else 0,
                             "total_s": total}
        return text

    def _chat_stream(self, url, payload, on_token, timeout):
        chunks = []
        with requests.post(url, json=payload, stream=True, timeout=timeout) as r:
            self._raise_for_status(r, None)
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = (obj.get("choices") or [{}])[0].get("delta", {})
                tok = delta.get("content")
                if tok:
                    chunks.append(tok)
                    if on_token:
                        on_token(tok)
        return "".join(chunks)

    # ---- embeddings ---------------------------------------------------------

    def embed(self, role, texts, timeout=120):
        """texts: list[str] -> list[list[float]]."""
        spec = self.config.model_for(role)
        url = spec["base_url"] + "/embeddings"
        payload = {"model": spec["model"], "input": texts}
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            self._raise_for_status(r, spec)
            data = r.json()["data"]
            # keep original order (some servers return an 'index' field)
            data = sorted(data, key=lambda d: d.get("index", 0))
            return [d["embedding"] for d in data]
        except requests.exceptions.ConnectionError:
            raise LLMError(self._conn_msg(spec))

    # ---- discovery ----------------------------------------------------------

    def list_models(self, base_url, timeout=10):
        """Return the model ids a provider currently serves (for /models)."""
        base_url = base_url.rstrip("/")
        try:
            r = requests.get(base_url + "/models", timeout=timeout)
            r.raise_for_status()
            # Ollama returns "data": null when no models are pulled yet.
            data = r.json().get("data") or []
            return [m["id"] for m in data]
        except requests.exceptions.ConnectionError:
            hint = ("Start its local server (Developer tab -> Start Server)."
                    if ":1234" in base_url
                    else "Make sure Ollama is running.")
            raise LLMError(f"Can't reach {base_url}. {hint}")
        except requests.exceptions.Timeout:
            raise LLMError(f"Timed out reaching {base_url}.")
        except Exception as e:
            raise LLMError(f"Couldn't list models at {base_url}: {e}")

    # ---- helpers ------------------------------------------------------------

    @staticmethod
    def _conn_msg(spec):
        prov = spec["provider"] if spec else "the server"
        url = spec["base_url"] if spec else "?"
        hint = ("Start its local server (Developer tab -> Start Server)."
                if prov == "lmstudio"
                else "Make sure Ollama is running (it usually starts on boot).")
        return f"Can't reach {prov} at {url}. {hint}"

    @staticmethod
    def _raise_for_status(r, spec):
        if r.status_code < 400:
            return
        body = ""
        try:
            body = r.text[:500]
        except Exception:
            pass
        model = f" for model '{spec['model']}'" if spec else ""
        raise LLMError(f"HTTP {r.status_code}{model}: {body}")
