"""Explicitly configured, stateless structured LLM calls; credentials stay in RAM."""
from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from ClinicalCDSGateway.gateway.common import Denied, strict_json


@dataclass
class ModelConfig:
    provider: str
    base_url: str
    model: str
    verifier_model: str
    api_key: str = field(default="", repr=False)

    @property
    def remote(self):
        hostname = urlsplit(self.base_url).hostname
        if hostname == "localhost":
            return False
        try:
            return not ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            return True

    def public(self):
        return {"provider": self.provider, "base_url": self.base_url, "model": self.model,
                "verifier_model": self.verifier_model, "remote": self.remote,
                "key_configured": bool(self.api_key), "configured": True}

    @classmethod
    def parse(cls, data):
        if type(data) is not dict or set(data) != {"provider", "base_url", "model", "verifier_model", "api_key"}:
            raise Denied("LLM_CONFIG_INVALID")
        if any(type(v) is not str for v in data.values()):
            raise Denied("LLM_CONFIG_INVALID")
        config = cls(**{key: value.strip() for key, value in data.items()})
        config.base_url = config.base_url.rstrip("/")
        config.verifier_model = config.verifier_model or config.model
        parsed = urlsplit(config.base_url)
        if (config.provider not in {"openai", "ollama", "compatible"}
                or not config.model or len(config.model) > 150 or len(config.verifier_model) > 150
                or any(c in config.api_key for c in "\r\n") or len(config.api_key) > 1000
                or parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname):
            raise Denied("LLM_CONFIG_INVALID")
        if parsed.scheme not in {"http", "https"} or (config.remote and parsed.scheme != "https"):
            raise Denied("LLM_CONFIG_INVALID")
        if config.provider == "openai" and config.base_url != "https://api.openai.com/v1":
            raise Denied("LLM_CONFIG_INVALID")
        if config.provider == "openai" and not config.api_key:
            raise Denied("LLM_CONFIG_INVALID")
        return config


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Denied("LLM_REDIRECT_DENIED")


def validate_schema(value, schema):
    kind = schema.get("type")
    if kind == "object":
        if type(value) is not dict or set(value) != set(schema["properties"]):
            raise Denied("LLM_RESPONSE_INVALID")
        for key, subschema in schema["properties"].items():
            validate_schema(value[key], subschema)
    elif kind == "array":
        if type(value) is not list or len(value) > min(120,schema.get("maxItems",120)):
            raise Denied("LLM_RESPONSE_INVALID")
        for item in value:
            validate_schema(item, schema["items"])
    elif kind == "string":
        if type(value) is not str or len(value) > min(32768,schema.get("maxLength",32768)):
            raise Denied("LLM_RESPONSE_INVALID")
    elif kind == "integer":
        if type(value) is not int or value < 0:
            raise Denied("LLM_RESPONSE_INVALID")
    elif kind == "boolean" and type(value) is not bool:
        raise Denied("LLM_RESPONSE_INVALID")
    if "enum" in schema and value not in schema["enum"]:
        raise Denied("LLM_RESPONSE_INVALID")


class StructuredClient:
    def __init__(self, config):
        self.config = config
        self.calls = 0
        self.metrics = []

    def complete(self, stage, instructions, payload, schema):
        cfg = self.config
        model = cfg.verifier_model if stage in {"verify", "privacy"} else cfg.model
        content = json.dumps(payload, ensure_ascii=False)
        common = instructions + "\nReturn only JSON matching this schema:\n" + json.dumps(schema, ensure_ascii=False)
        if cfg.provider == "openai":
            endpoint = cfg.base_url + "/responses"
            body = {"model": model, "instructions": common, "input": content, "store": False,
                    "max_output_tokens": 6000, "text": {"format": {"type": "json_schema", "name": "clinical_" + stage, "strict": True, "schema": schema}}}
        elif cfg.provider == "ollama":
            endpoint = cfg.base_url + "/api/chat"
            # The laptop demo uses a bounded context; never silently truncate a
            # medical source. UTF-8 bytes form a conservative token upper bound.
            if len((common + content).encode("utf-8")) + 256 > 16384 - 4096:
                raise Denied("LLM_CONTEXT_LIMIT")
            body = {"model": model, "stream": False, "messages": [{"role": "system", "content": common}, {"role": "user", "content": content}],
                    "format": schema, "keep_alive": "5m", "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 4096}}
            if model.lower().startswith("qwen3"):
                body["think"] = False
        else:
            endpoint = cfg.base_url + "/chat/completions"
            body = {"model": model, "messages": [{"role": "system", "content": common}, {"role": "user", "content": content}],
                    "temperature": 0, "max_tokens": 6000,
                    "response_format": {"type": "json_schema", "json_schema": {"name": "clinical_" + stage, "strict": True, "schema": schema}}}
        headers = {"Content-Type": "application/json"}
        if cfg.api_key:
            headers["Authorization"] = "Bearer " + cfg.api_key
        try:
            started = time.perf_counter()
            req = Request(endpoint, data=json.dumps(body).encode(), headers=headers, method="POST")
            self.calls += 1
            with build_opener(NoRedirect()).open(req, timeout=600 if cfg.provider == "ollama" else 120) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise Denied("LLM_RESPONSE_INVALID")
            response = strict_json(raw, max_bytes=1_000_000)
            if cfg.provider == "openai":
                if response.get("status") not in {None, "completed"}:
                    raise Denied("LLM_RESPONSE_INCOMPLETE")
                text = "".join(part["text"] for item in response.get("output", []) if item.get("type") == "message"
                               for part in item.get("content", []) if part.get("type") == "output_text")
            elif cfg.provider == "ollama":
                if response.get("done") is False or response.get("done_reason") in {"length", "max_tokens"}:
                    raise Denied("LLM_RESPONSE_INCOMPLETE")
                text = response["message"]["content"]
            else:
                choice = response["choices"][0]
                if choice.get("finish_reason") not in {None, "stop"}:
                    raise Denied("LLM_RESPONSE_INCOMPLETE")
                text = choice["message"]["content"]
            result = strict_json(text.encode(), max_bytes=200_000)
            validate_schema(result, schema)
            self.metrics.append({"stage": stage, "model": model, "seconds": round(time.perf_counter() - started, 3),
                                 "input_tokens": response.get("prompt_eval_count"), "output_tokens": response.get("eval_count"),
                                 "load_seconds": round(response.get("load_duration", 0) / 1e9, 3)})
            return result
        except HTTPError as error:
            code = "LLM_AUTH_FAILED" if error.code in {401, 403} else "LLM_RATE_LIMIT" if error.code == 429 else "LLM_API_FAILED"
            raise Denied(code) from None
        except (URLError, TimeoutError, OSError):
            raise Denied("LLM_UNAVAILABLE") from None
        except (KeyError, TypeError, IndexError, ValueError, UnicodeError):
            raise Denied("LLM_RESPONSE_INVALID") from None

    def probe(self):
        result = self.complete("probe", "Return ready=true. No document is being processed.", {"test": "connection"},
                               {"type": "object", "properties": {"ready": {"type": "boolean"}}, "required": ["ready"], "additionalProperties": False})
        if result["ready"] is not True:
            raise Denied("LLM_RESPONSE_INVALID")
        return {"connected": True, **self.config.public()}
