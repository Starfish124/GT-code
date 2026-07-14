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
import re

import requests


class LLMError(Exception):
    """Friendly, user-facing error (bad connection, model not loaded, etc.)."""


_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*$", re.I)


def _parse_seconds(value, default):
    """Tolerant seconds parse for performance.llm_timeout.

    The key sits directly under keep_alive: 8h in config.yaml, so users will
    naturally write llm_timeout: 30m — accept plain numbers and s/m/h duration
    strings, and fall back to the default on anything else instead of killing
    every turn with a raw int() traceback.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    m = _DURATION.match(str(value or ""))
    if not m:
        return default
    mult = {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2).lower()]
    return int(float(m.group(1)) * mult)


def _looks_like_read_timeout(exc) -> bool:
    """Did this ConnectionError actually START as a read timeout?

    requests re-raises urllib3's ReadTimeoutError from iter_content() as a
    ConnectionError once streaming has begun — so a mid-generation stall
    (machine starts swapping after the first token) must be recognised here,
    or the user is told "can't reach Ollama" about a server that is fine.
    """
    try:
        from urllib3.exceptions import ReadTimeoutError
    except ImportError:
        ReadTimeoutError = ()
    seen, stack = set(), [exc]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, ReadTimeoutError):
            return True
        stack.extend((e.__cause__, e.__context__))
        stack.extend(a for a in getattr(e, "args", ())
                     if isinstance(a, BaseException))
    return "read timed out" in str(exc).lower()


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
             on_token=None, timeout=None, tools=None):
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
        if timeout is None:
            # On a CPU-only box, model load + prompt prefill can run 10+
            # minutes with ZERO bytes on the wire (Ollama sends nothing until
            # the first token) — a live turn died at 600s while the prefill
            # was legitimately at ~790s. Default high; esc/Ctrl-C is the way
            # to bail early, not a network timeout.
            timeout = _parse_seconds(self._perf("llm_timeout", 1800), 1800)
        spec = self.config.model_for(role)
        self.last_metrics = None
        self.last_tool_calls = []
        try:
            return self._chat_ollama(spec, messages, stream, temperature,
                                     on_token, timeout, tools)
        except requests.exceptions.ConnectionError as e:
            # A read timeout AFTER streaming starts arrives wrapped as a
            # ConnectionError (requests re-raises it from iter_content) —
            # that is a stall, not a dead server; diagnose it as one.
            if _looks_like_read_timeout(e):
                raise LLMError(self._timeout_msg(spec, timeout))
            raise LLMError(self._conn_msg(spec))
        except requests.exceptions.Timeout:
            raise LLMError(self._timeout_msg(spec, timeout))

    @staticmethod
    def _timeout_msg(spec, timeout):
        return (
            f"'{spec['model']}' produced nothing for {timeout}s — on a "
            "CPU-only machine this usually means the model is too big "
            "for free RAM (swapping). Try again (the model may be loaded "
            "now), close RAM-heavy apps, or run this turn on the resident "
            "small model with /model tiny. Raise performance.llm_timeout "
            "in config.yaml if the machine genuinely needs longer.")

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

        # (connect, read) split: a server that is DOWN still fails in seconds;
        # only the wait for bytes from a live server gets the long budget.
        timeout = (10, timeout)

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
