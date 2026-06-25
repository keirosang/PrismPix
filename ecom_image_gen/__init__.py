# -*- coding: utf-8 -*-
"""
PrismPix — AI 电商视觉生成引擎
==================================================

输入:
    - 产品图片 (必需)
    - 产品基础信息 (类目 / 风格 / 模特属性)
    - API Key / Base URL / 模型名称 (OpenAI compatible)

对每个 SKU 自动生成:
    1. 产品结构分析 JSON          (Stage 1, vision model)
    2. 营销策略 JSON              (Stage 2)
    3. 14 条 images.edit Prompt  (Stage 3, H1-H5 + D1-D9, 集成 25 场景模板)
    4. 自动生成 14 张电商图        (可选, images.edit API)
    5. 结构化输出目录

用法:
    Web:  python main.py  (浏览器打开 http://127.0.0.1:8000)

模块结构:
    ecom_image_gen/
        config.py              - Config, ProductInput, load_config()
        logging_setup.py       - setup_logging()
        json_utils.py          - extract_json, validate_schema, write/read_json
        cache.py               - PromptCache (线程安全 LLM 调用缓存)
        client.py              - build_client, with_retry, call_json_model
        image_utils.py         - encode_image, to_image_ref, prepare_image_for_edit
        prompt_templates.py    - ModuleSpec, PROMPT_MODULES, render_stage3_instruction
        template_engine.py     - 加载 prompt_templates/ 下 25 个场景模板 JSON
        module_template_map.py - H1-D9 → 25 场景模板映射 (按品类/风格自适应)
        stage1.py              - stage1_analyze_image()
        stage2.py              - stage2_generate_campaign()
        stage3.py              - stage3_generate_prompts() (注入模板上下文)
        image_gen.py           - generate_image/edit(), generate_all_images()
        workspace.py           - build_sku_workspace(), save_outputs()
        runner.py              - run_sku(), run_batch()
        web.py                 - run_web()
"""

from ecom_image_gen.cache import PromptCache
from ecom_image_gen.client import build_client, call_json_model, hash_key, with_retry
from ecom_image_gen.config import Config, ProductInput, load_config
from ecom_image_gen.image_gen import generate_all_images, generate_image, generate_lookbook
from ecom_image_gen.image_utils import encode_image, prepare_image_for_edit, to_image_ref
from ecom_image_gen.json_utils import extract_json, read_json, validate_schema, write_json
from ecom_image_gen.logging_setup import LOG, setup_logging
from ecom_image_gen.module_template_map import get_module_map, resolve_category
from ecom_image_gen.prompt_templates import (
    LOOKBOOK_CODES,
    LOOKBOOK_MODULES,
    MODULE_CODES,
    PROMPT_FIELDS,
    PROMPT_MODULES,
    ModuleSpec,
    get_modules_for_mode,
    render_prompts_markdown,
    render_stage3_instruction,
)
from ecom_image_gen.style_lock import generate_style_lock, render_style_lock_text
from ecom_image_gen.runner import run_batch, run_sku
from ecom_image_gen.stage1 import STAGE1_SCHEMA_KEYS, stage1_analyze_image
from ecom_image_gen.stage2 import STAGE2_SCHEMA_KEYS, stage2_generate_campaign
from ecom_image_gen.stage3 import stage3_generate_prompts
from ecom_image_gen.template_engine import TemplateEngine
from ecom_image_gen.web import run_web
from ecom_image_gen.workspace import build_sku_workspace, save_outputs

__all__ = [
    # config
    "Config",
    "ProductInput",
    "load_config",
    # logging
    "LOG",
    "setup_logging",
    # json utils
    "extract_json",
    "read_json",
    "validate_schema",
    "write_json",
    # image utils
    "encode_image",
    "to_image_ref",
    "prepare_image_for_edit",
    # client
    "build_client",
    "call_json_model",
    "hash_key",
    "with_retry",
    # cache
    "PromptCache",
    # prompt templates
    "MODULE_CODES",
    "ModuleSpec",
    "PROMPT_MODULES",
    "PROMPT_FIELDS",
    "render_prompts_markdown",
    "render_stage3_instruction",
    # template engine
    "TemplateEngine",
    "get_module_map",
    "resolve_category",
    # style lock
    "generate_style_lock",
    "render_style_lock_text",
    # stage 1
    "STAGE1_SCHEMA_KEYS",
    "stage1_analyze_image",
    # stage 2
    "STAGE2_SCHEMA_KEYS",
    "stage2_generate_campaign",
    # stage 3
    "stage3_generate_prompts",
    # image gen
    "generate_all_images",
    "generate_image",
    # workspace
    "build_sku_workspace",
    "save_outputs",
    # runner
    "run_batch",
    "run_sku",
    # web
    "run_web",
]
