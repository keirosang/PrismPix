# -*- coding: utf-8 -*-
"""
任务编排 — 单 SKU 全流程 + Multi-SKU 批处理

run_sku():  三阶段 + 可选出图, 支持断点续跑
run_batch(): 从 JSON 批处理文件读取多 SKU 并依次处理
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from ecom_image_gen.cache import PromptCache
from ecom_image_gen.client import build_client
from ecom_image_gen.config import Config, ProductInput
from ecom_image_gen.image_gen import generate_all_images, generate_lookbook
from ecom_image_gen.json_utils import read_json, validate_schema
from ecom_image_gen.logging_setup import LOG
from ecom_image_gen.stage1 import STAGE1_SCHEMA_KEYS, stage1_analyze_image
from ecom_image_gen.stage2 import STAGE2_SCHEMA_KEYS, stage2_generate_campaign
from ecom_image_gen.stage3 import stage3_generate_prompts
from ecom_image_gen.workspace import build_sku_workspace, save_outputs


def run_sku(
    cfg: Config,
    product_input: ProductInput,
    progress_callback: Optional[Callable[[str, Any], None]] = None,
) -> dict:
    """对单个 SKU 跑完三阶段 (+ 可选出图), 返回结果摘要。

    断点续跑策略:
        - product.json 存在且非 force 模式 → 复用
        - campaign.json 存在且非 force 模式 → 复用
        - prompts.json 存在且非 force 模式 → 复用
        - 图片存在且非 force 模式 → 跳过 (generate_all_images 内部处理)

    Args:
        cfg: 全局配置。
        product_input: 单个 SKU 的输入信息。
        progress_callback: 可选进度回调 (stage, data_dict)。

    Returns:
        摘要 dict: {sku, workspace, product_name, prompts, images}
    """
    def _progress(stage: str, **data: Any) -> None:
        if progress_callback:
            try:
                progress_callback(stage, data)
            except Exception:
                pass

    sku = product_input.safe_sku()
    LOG.info("=" * 60)
    LOG.info("开始处理 SKU: %s", sku)

    _progress("start", sku=sku)

    ws = build_sku_workspace(cfg, sku)
    cache = PromptCache(ws / "prompt_cache.json",
                        enabled=cfg.use_prompt_cache and not cfg.force)
    client = build_client(cfg)

    # ---- Stage 1: 复用已有 product.json (断点续跑) ----
    product_path = ws / "product.json"
    _progress("stage1", status="running")
    if product_path.exists() and not cfg.force:
        LOG.info("复用已有 product.json")
        product = read_json(product_path)
        _progress("stage1", status="cached")
    else:
        product = stage1_analyze_image(
            client, cfg, product_input.image, product_input, cache=cache,
        )
        product = validate_schema(product, STAGE1_SCHEMA_KEYS, "product.json")
        _progress("stage1", status="done",
                  product_name=product.get("product_name", ""))

    # ---- Stage 2: 复用已有 campaign.json ----
    campaign_path = ws / "campaign.json"
    _progress("stage2", status="running")
    if campaign_path.exists() and not cfg.force:
        LOG.info("复用已有 campaign.json")
        campaign = read_json(campaign_path)
        _progress("stage2", status="cached")
    else:
        campaign = stage2_generate_campaign(client, cfg, product, cache=cache)
        campaign = validate_schema(campaign, STAGE2_SCHEMA_KEYS, "campaign.json")
        _progress("stage2", status="done",
                  selling_point=campaign.get("core_selling_point", ""))

    # ---- Stage 3: prompts.json 可复用 (需校验模式匹配) ----
    prompts_path = ws / "prompts.json"
    from ecom_image_gen.prompt_templates import get_modules_for_mode
    expected_codes = {m.code for m in get_modules_for_mode(cfg.generation_mode)}

    # 如果只要求跑到 Stage2, 提前返回
    if cfg.generation_mode == "__stage2__":
        result = {
            "sku": sku,
            "workspace": str(ws),
            "product_name": product.get("product_name"),
            "product": product,
            "campaign": campaign,
        }
        _progress("done", result=result)
        LOG.info("Stage1+2 完成 (stop_at_stage=2): %s", sku)
        return result

    _progress("stage3", status="running")
    if prompts_path.exists() and not cfg.force:
        cached = read_json(prompts_path)
        cached_codes = {k for k in cached if k[0] in "HDML" and len(k) <= 3}
        if expected_codes.issubset(cached_codes):
            LOG.info("复用已有 prompts.json (模式=%s)", cfg.generation_mode)
            prompts = cached
            _progress("stage3", status="cached", count=len(prompts))
        else:
            LOG.info("prompts.json 模式不匹配 (缓存=%s 需要=%s), 重新生成",
                     cached_codes, expected_codes)
            prompts = None
    else:
        prompts = None

    if prompts is None:
        prompts = stage3_generate_prompts(
            client, cfg, product, campaign,
            category=product_input.category,
            style=product_input.style,
            additional_requirements=product_input.additional_requirements,
            platform=product_input.platform,
            language=product_input.language,
            generation_mode=cfg.generation_mode,
            model_attrs=product_input.model_attrs,
            model_scene=product_input.model_scene,
            shooting_style=product_input.shooting_style,
            face_visible=product_input.face_visible,
            cache=cache,
        )
        _progress("stage3", status="done", count=len(prompts))

    # ---- 保存文本产物 ----
    save_outputs(ws, product, campaign, prompts, sku)

    # ---- 图片生成 (可选) ----
    img_stats = {"generated": 0, "skipped": 0, "failed": 0}
    if cfg.enable_generate_images:
        _progress("images", status="running", total=0, done=0)
        if cfg.generation_mode == "lookbook":
            img_stats = generate_lookbook(
                client, cfg, prompts, product_input.image, ws,
                model_attrs=product_input.model_attrs,
                model_scene=product_input.model_scene,
                shooting_style=product_input.shooting_style,
                face_visible=product_input.face_visible,
            )
        else:
            img_stats = generate_all_images(
                client, cfg, prompts, product_input.image, ws,
                category=product_input.category,
            )
        _progress("images", status="done", **img_stats)
    else:
        LOG.info("已禁用图片生成 (--no-images), 仅产出 prompt")
        _progress("images", status="skipped")

    result = {
        "sku": sku,
        "workspace": str(ws),
        "product_name": product.get("product_name"),
        "prompts": len(prompts),
        "images": img_stats,
    }
    _progress("done", result=result)
    LOG.info("SKU 完成: %s -> %s", sku, ws)
    return result


def run_batch(cfg: Config, batch_file: str) -> list[dict]:
    """从 JSON 批处理文件读取多个 SKU 并依次处理。

    batch.json 形如:
        [
          {"sku":"A001","image":"a.jpg","category":"女装","style":"简约","model_attrs":"亚洲女性"},
          {"sku":"B002","image":"https://.../b.png","category":"鞋靴"}
        ]

    Args:
        cfg: 全局配置。
        batch_file: 批处理 JSON 文件路径。

    Returns:
        每个 SKU 的结果摘要列表, 含 error 字段表示失败。
    """
    items = read_json(Path(batch_file))
    if not isinstance(items, list):
        raise ValueError("batch 文件必须是 SKU 对象数组")

    summaries: list[dict] = []
    for raw in items:
        pi = ProductInput(
            sku=raw.get("sku") or raw.get("name") or "SKU",
            image=raw["image"],
            category=raw.get("category", ""),
            style=raw.get("style", ""),
            model_attrs=raw.get("model_attrs", ""),
        )
        try:
            summaries.append(run_sku(cfg, pi))
        except Exception as e:
            LOG.error("SKU 处理失败 [%s]: %s", pi.sku, e)
            LOG.debug(traceback.format_exc())
            summaries.append({"sku": pi.sku, "error": str(e)})

    return summaries
