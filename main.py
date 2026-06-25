#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2026 PrismPix. Licensed under GPL v3.
# See LICENSE file in the project root for full license text.
"""
PrismPix — AI 电商视觉生成引擎

启动: python main.py
浏览器打开 http://127.0.0.1:8000

所有配置通过 .env 文件或环境变量设置。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback

from ecom_image_gen.config import load_config
from ecom_image_gen.logging_setup import LOG, setup_logging
from ecom_image_gen.web import run_web


def main() -> int:
    """启动 Web 服务。

    配置:
        WEB_HOST   — 监听地址 (默认 127.0.0.1)
        WEB_PORT   — 监听端口 (默认 8000)
        其他配置见 .env.example

    Returns:
        退出码 (0 成功, 130 用户中断, 1 失败)。
    """
    cfg = load_config()

    level = getattr(logging, cfg.log_level, logging.INFO)
    setup_logging(level=level)
    LOG.info("运行配置: %s", json.dumps(cfg.masked(), ensure_ascii=False))

    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_PORT", "8000"))

    try:
        run_web(cfg, host=host, port=port)
    except KeyboardInterrupt:
        LOG.warning("用户中断")
        return 130
    except Exception as e:
        LOG.error("致命错误: %s", e)
        LOG.debug(traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
