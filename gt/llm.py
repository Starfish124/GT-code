"""Client for the local Ollama server.

GT talks to Ollama's NATIVE /api/chat endpoint: that unlocks keep_alive (models
stay hot in RAM between requests instead of being evicted), think=false (Qwen3's
hidden reasoning mode off by default — the single biggest latency win), and
precise per-request metrics (model load time, prefill time, generation tok/s)
that GT surfaces to the user — and native function calling (tools=[...]) on
models whose chat template supports it (see supports_tools). Embeddings and
model listing use Ollama's OpenAI-compatible /v1 endpoints.
"""

import json
import requests


class LLMError(Exception):
    """Friendly, user-facing error (bad connection, model not loaded, etc.)."""


class LLM:
    def __init__(self, config):
        self.config = config
        self.last_metrics = None  # timing of the most recent chat call
        self.last_tool_calls = []  # native tool calls from the most recent chat
        self._tools_cache = {}     # (base_url, model) -> supports native tools?

    def _perf(self, key, default):
        return getattr(self.config, "performance", {}).get(key, default)

    # ---- chat ---------------------------------------------------------------

    def chat(self, role, messages, stream=True, temperature=None,
             on_token=None, timeout=600, tools=None):
        """Return the assistant's full text. If stream=True, on_token(str) is
        called with each incremental chunk as it arrives. Timing details of
        the call land in self.last_metrics.

        temperature=None uses the configured task default (performance.
        temperature); callers pass an explicit value to override — the agent
        runs conversation warmer than code, the router classifies at 0.

        tools: optional list of native function-calling specs (Tool.spec()).
        Any tool calls the model emits land in self.last_tool_calls, in
        Ollama's raw shape ([{"function": {"name", "arguments"}}, ...])."""
        if temperature is None:
            temperature = float(self._perf("temperature", 0.3))
        spec = self.config.model_for(role)
        self.last_metrics = None
        self.last_tool_calls = []
        try:
            return self._chat_ollama(spec, messages, stream, temperature,
                                     on_token, timeout, tools)
        except requests.exceptions.ConnectionError:
            raise LLMError(self._conn_msg(spec))
        except requests.exceptions.Timeout:
            raise LLMError(f"'{spec['model']}' timed out after {timeout}s.")

    def supports_tools(self, role):
        """Does this role's model support NATIVE function calling?

        Asks Ollama's /api/show once per model and caches the answer: modern
        Ollamas list the model's capabilities ("tools" among them) straight
        from its chat template. Anything unclear — old Ollama, no capabilities
        field, unreachable — is False, so GT falls back to the prompt-JSON
        protocol that works everywhere."""
        try:
            spec = self.config.model_for(role)
        except KeyError:
            return False
        base = spec["base_url"]
        base = base[:-3] if base.endswith("/v1") else base
        key = (base, spec["model"])
        if key not in self._tools_cache:
            supported = False
            try:
                r = requests.post(base + "/api/show",
                                  json={"model": spec["model"]}, timeout=10)
                if r.status_code < 400:
                    caps = r.json().get("capabilities") or []
                    supported = "tools" in caps
            except Exception:
                supported = False
            self._tools_cache[key] = supported
        return self._tools_cache[key]

    # ---- Ollama native path ---------------------------------------------------

    def _chat_ollama(self, spec, messages, stream, temperature, on_token,
                     timeout, tools=None):
        base = spec["base_url"]
        base = base[:-3] if base.endswith("/v1") else base
        url = base + "/api/chat"

        think = bool(self._perf("thinking", False))
        model = spec["model"]
        if not think and "qwen3" in model.lower():
            # belt & braces: Qwen3's documented soft switch, for older Ollamas
            # that ignore the think parameter. Appended to the SYSTEM message
            # (not the newest user message) so the token prefix stays stable
            # and Ollama's KV cache survives between agent steps.
            messages = [dict(m) for m in messages]
            for m in messages:
                if m["role"] == "system":
                    m["content"] = m["content"] + "\n/no_think"
                    break
            else:
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
        if tools:
            payload["tools"] = tools

        if not stream:
            r = requests.post(url, json=payload, timeout=timeout)
            self._raise_for_status(r, spec)
            obj = r.json()
            self.last_metrics = self._ollama_metrics(obj)
            msg = obj.get("message") or {}
            self.last_tool_calls.extend(msg.get("tool_calls") or [])
            return msg.get("content", "") or ""

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
                msg = obj.get("message") or {}
                # Tool calls stream as their own chunks alongside any text.
                self.last_tool_calls.extend(msg.get("tool_calls") or [])
                tok = msg.get("content", "")
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
            raise LLMError(f"Can't reach {base_url}. Make sure Ollama is running.")
        except requests.exceptions.Timeout:
            raise LLMError(f"Timed out reaching {base_url}.")
        except Exception as e:
            raise LLMError(f"Couldn't list models at {base_url}: {e}")

    # ---- helpers ------------------------------------------------------------

    @staticmethod
    def _conn_msg(spec):
        url = spec["base_url"] if spec else "?"
        return (f"Can't reach Ollama at {url}. Make sure Ollama is running "
                "(it usually starts on boot).")

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
