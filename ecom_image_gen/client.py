# -*- coding: utf-8 -*-
"""
OpenAI 客户端 — 构建 / 重试 / JSON 调用 (兼容 OpenAI / OneAPI / NewAPI / OpenRouter)
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Callable, Optional

from ecom_image_gen.cache import PromptCache
from ecom_image_gen.config import Config
from ecom_image_gen.json_utils import extract_json
from ecom_image_gen.logging_setup import LOG


def build_client(cfg: Config):
    """构造 openai SDK v1.x 客户端。

    Args:
        cfg: 全局配置, 至少包含 api_key, base_url, request_timeout。

    Returns:
        openai.OpenAI 实例。

    Raises:
        RuntimeError: openai 库未安装或 api_key 缺失。
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError(
            "缺少 openai 库, 请先安装:  pip install openai"
        ) from None

    if not cfg.api_key:
        raise RuntimeError("缺少 API Key (请在 .env 中设置 OPENAI_API_KEY)")

    return OpenAI(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        timeout=cfg.request_timeout,
        max_retries=0,  # 重试由 with_retry 控制
    )


def with_retry(
    fn: Callable,
    *,
    max_retries: int,
    backoff: float,
    desc: str = "request",
) -> Any:
    """带指数退避的重试包装。

    Args:
        fn: 无参数 callable, 每次调用尝试执行一次。
        max_retries: 最多尝试次数 (>= 2)。
        backoff: 退避基数 (秒), 第 n 次重试等待 backoff^(n-1) 秒。
        desc: 描述标签, 用于日志。

    Returns:
        fn() 的返回值。

    Raises:
        RuntimeError: 重试耗尽后仍失败。
    """
    last_err: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            wait = backoff ** (attempt - 1)
            LOG.warning(
                "[%s] 第 %d/%d 次失败: %s (等待 %.1fs 重试)",
                desc, attempt, max_retries, e, wait,
            )
            if attempt < max_retries:
                time.sleep(wait)

    raise RuntimeError(
        f"[{desc}] 重试 {max_retries} 次仍失败: {last_err}"
    ) from last_err


def hash_key(*parts: str) -> str:
    """把任意字符串片段拼成稳定的短 hash, 用于缓存键。

    Args:
        *parts: 任意数量的字符串片段。

    Returns:
        32 字符的十六进制 hash。
    """
    blob = "||".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def call_json_model(
    client,
    cfg: Config,
    model: str,
    messages: list,
    desc: str = "chat",
    cache: Optional[PromptCache] = None,
    cache_key: str = "",
) -> dict:
    """调用 chat completions 并解析为 JSON dict (带缓存 + 重试)。

    优先使用 json_object 响应格式; 失败则降级为普通文本解析。

    Args:
        client: openai.OpenAI 实例。
        cfg: 全局配置。
        model: 要使用的模型名 (vision / text 可不同)。
        messages: chat messages 列表。
        desc: 日志描述标签。
        cache: 可选的 PromptCache 实例。
        cache_key: 缓存键 (由调用方给出, 保证语义稳定)。

    Returns:
        解析后的 JSON dict。
    """
    if cache and cache_key:
        hit = cache.get(cache_key)
        if hit is not None:
            LOG.info("[%s] 命中本地缓存", desc)
            return hit

    def _call() -> dict:
        # 优先使用 json_object 响应格式, max_tokens 防止长输出被截断
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.7,
                max_tokens=16000,
            )
        except Exception as e:
            LOG.debug("[%s] json_object 模式不支持, 降级普通模式: %s", desc, e)
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                max_tokens=16000,
            )

        content = resp.choices[0].message.content or ""
        finish = resp.choices[0].finish_reason or "unknown"

        LOG.info("[%s] 响应: %d chars, finish_reason=%s",
                 desc, len(content), finish)

        if finish == "length":
            LOG.warning("[%s] 输出被截断 (finish_reason=length)! "
                        "考虑增大 max_tokens 或精简输入。", desc)

        if len(content) < 100:
            LOG.warning("[%s] 响应异常短 (%d chars): %s", desc, len(content), content)

        result = extract_json(content)
        result_keys = list(result.keys()) if isinstance(result, dict) else "not-dict"
        LOG.info("[%s] extract_json → type=%s top_keys=%s",
                 desc, type(result).__name__, result_keys)
        return result

    result = with_retry(
        _call,
        max_retries=cfg.max_retries,
        backoff=cfg.retry_backoff,
        desc=desc,
    )

    if cache and cache_key:
        cache.set(cache_key, result)

    return result
