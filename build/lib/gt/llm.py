"""Unified client for any OpenAI-compatible endpoint (Ollama + LM Studio).

Both Ollama (:11434/v1) and LM Studio (:1234/v1) speak the OpenAI REST shape,
so one code path handles every model. Supports streaming chat + embeddings.
"""

import json
import requests


class LLMError(Exception):
    """Friendly, user-facing error (bad connection, model not loaded, etc.)."""


class LLM:
    def __init__(self, config):
        self.config = config

    # ---- chat ---------------------------------------------------------------

    def chat(self, role, messages, stream=True, temperature=0.3,
             on_token=None, timeout=600):
        """Return the assistant's full text. If stream=True, on_token(str) is
        called with each incremental chunk as it arrives."""
        spec = self.config.model_for(role)
        url = spec["base_url"] + "/chat/completions"
        payload = {
            "model": spec["model"],
            "messages": messages,
            "temperature": temperature,
            "stream": bool(stream),
        }
        try:
            if stream:
                return self._chat_stream(url, payload, on_token, timeout)
            r = requests.post(url, json=payload, timeout=timeout)
            self._raise_for_status(r, spec)
            return r.json()["choices"][0]["message"]["content"] or ""
        except requests.exceptions.ConnectionError:
            raise LLMError(self._conn_msg(spec))
        except requests.exceptions.Timeout:
            raise LLMError(f"'{spec['model']}' ({spec['provider']}) timed out "
                           f"after {timeout}s.")

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
