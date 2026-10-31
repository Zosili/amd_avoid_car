# -*- coding: utf-8 -*-
"""vision/vision_provider.py — Vision JSON 解析校验 + 场景回放"""
import protocol


class VisionProvider:
    """对上层只暴露一个接口：poll() -> 合法 vision dict / None
    畸形帧丢日志不计入有效性；连续无有效帧会让 timestamp 过期 → R5 停。"""

    def __init__(self, source=None, on_drop=None):
        self._source = source          # sim: [(t_ms, dict|None)]；live 由外部 push
        self._cur = None
        self._i = 0
        self._t0 = None
        self._on_drop = on_drop

    def push(self, raw):
        """live 模式：欧阳每来一帧调一次。raw 是 dict 或 JSON 字符串"""
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        if isinstance(raw, str):
            try:
                raw = __import__("json").loads(raw)
            except Exception:
                self._drop("json parse failed")
                return
        v = protocol.parse_vision(raw)
        if v is None:
            self._drop("schema invalid")
            return
        v["t"] = protocol.now_ms()
        self._cur = v
        return v

    def _drop(self, why):
        if self._on_drop:
            self._on_drop(why)

    def poll(self, now=None):
        # sim 模式回放
        if self._source is not None:
            now = protocol.now_ms() if now is None else now
            if self._t0 is None:
                self._t0 = now
            rel = now - self._t0
            while self._i < len(self._source) and self._source[self._i][0] <= rel:
                v = protocol.parse_vision(self._source[self._i][1]) if self._source[self._i][1] else None
                if v is not None:
                    v["t"] = self._t0 + self._source[self._i][0]
                self._cur = v
                self._i += 1
        return dict(self._cur) if self._cur else None
