# -*- coding: utf-8 -*-
"""
图片生成模块 — images.edit API + 并发生成 + 断点续跑

使用 OpenAI images.edit 接口: 原始产品图作为底图,
prompt 描述期望的场景/环境, 产品外观自动保留。

兼容 OpenAI / OneAPI / NewAPI / OpenRouter。
"""

from __future__ import annotations

import concurrent.futures
import threading
from pathlib import Path

from ecom_image_gen.client import with_retry
from ecom_image_gen.config import Config
from ecom_image_gen.image_utils import prepare_image_for_edit
from ecom_image_gen.logging_setup import LOG
from ecom_image_gen.prompt_templates import PROMPT_MODULES


def generate_image(
    client,
    cfg: Config,
    prompt_obj: dict,
    product_image: str,
    out_path: Path,
    desc: str = "image",
) -> Path:
    """使用 images.edit API 生成单张电商图。

    原始产品图作为底图 (image 参数), prompt 描述场景/环境/灯光/构图。
    产品形状、颜色、材质由底图自动保留。

    Args:
        client: openai.OpenAI 实例。
        cfg: 全局配置 (提供 image_model, max_retries 等)。
        prompt_obj: 单条 prompt 对象 (含 size, prompt, camera 等字段)。
        product_image: 原始产品图片路径 (本地文件或 http URL)。
        out_path: 输出图片路径 (如 H1.png)。
        desc: 日志描述标签。

    Returns:
        输出文件 Path。
    """
    size = prompt_obj.get("size") or "1024x1024"
    prompt_text = _compose_edit_prompt(prompt_obj)

    def _call() -> bytes:
        # 准备图片文件 (本地直接读, URL 下载到临时文件)
        img_path = prepare_image_for_edit(product_image)
        try:
            with open(img_path, "rb") as img_file:
                resp = client.images.edit(
                    image=img_file,
                    prompt=prompt_text,
                    model=cfg.image_model,
                    size=size,
                    n=1,
                )
            data = resp.data[0]
            raw = _decode_image_payload(data)
            if raw is None:
                raise RuntimeError("images.edit 返回无图片数据")
            return raw
        finally:
            # 清理临时下载文件 (本地文件不受影响)
            if product_image.startswith(("http://", "https://")):
                try:
                    img_path.unlink(missing_ok=True)
                except Exception:
                    pass

    raw = with_retry(
        _call,
        max_retries=cfg.max_retries,
        backoff=cfg.retry_backoff,
        desc=desc,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    LOG.info("[%s] 已保存: %s (%d bytes)", desc, out_path.name, len(raw))
    return out_path


def generate_all_images(
    client,
    cfg: Config,
    prompts: dict,
    product_image: str,
    ws: Path,
    progress_callback: Any = None,
    category: str = "",
) -> dict:
    """并发生成电商图; 已存在的图自动跳过 (断点续跑)。

    生成前先产出「产品身份证」参考图 (根据品类自适应展示方式),
    后续所有 H/D 图以此参考图为底图, 确保产品一致性。

    Args:
        category: 产品类目, 用于决定参考图展示方式
                  (服装=人台, 配饰=手模/耳模, 其他=浮空)
        progress_callback: 可选, 每完成一张图时调用 callback(code, status, path)。
    """
    # ── Step 0: 产品身份证参考图 ──
    ref_path = ws / "product_ref.png"
    ref_exists = ref_path.exists() and ref_path.stat().st_size > 0 and not cfg.force

    if not ref_exists:
        LOG.info("生成产品身份证参考图 (正/背/侧 + 3 细节)...")
        ref_prompt = _build_product_ref_prompt(prompts, category)
        try:
            generate_image(
                client, cfg, ref_prompt, product_image, ref_path,
                desc="product_ref",
            )
            if progress_callback:
                try:
                    progress_callback("product_ref", "done", str(ref_path))
                except Exception:
                    pass
        except Exception as e:
            LOG.warning("产品参考图生成失败: %s, 回退到原始产品图", e)
            ref_path = None  # 回退
    else:
        LOG.info("复用已有产品参考图: %s", ref_path)
        if progress_callback:
            try:
                progress_callback("product_ref", "cached", str(ref_path))
            except Exception:
                pass

    # 底图: 优先用参考图, 失败则用原图
    base_image = str(ref_path) if ref_path and ref_path.exists() else product_image

    # ── Step 1: 主图 & 详情图 ──
    todo: list[tuple[str, dict, Path]] = []
    skipped = 0

    for spec in PROMPT_MODULES:
        out_path = ws / f"{spec.code}.png"
        if out_path.exists() and out_path.stat().st_size > 0 and not cfg.force:
            skipped += 1
            continue
        todo.append((spec.code, prompts[spec.code], out_path))

    if skipped:
        LOG.info("断点续跑: 跳过 %d 张已生成图片", skipped)
    if not todo:
        LOG.info("全部图片已存在, 无需生成")
        return {"generated": 0, "skipped": skipped, "failed": 0,
                "product_ref": str(ref_path) if ref_path else ""}

    results = {"generated": 0, "skipped": skipped, "failed": 0,
               "product_ref": str(ref_path) if ref_path else ""}
    lock = threading.Lock()
    cb = progress_callback

    if cb and skipped:
        for spec in PROMPT_MODULES:
            p = ws / f"{spec.code}.png"
            if p.exists() and p.stat().st_size > 0:
                try:
                    cb(spec.code, "cached", str(p))
                except Exception:
                    pass

    def _work(code: str, prompt_obj: dict, out_path: Path) -> None:
        try:
            generate_image(
                client, cfg, prompt_obj, base_image, out_path,
                desc=f"img:{code}",
            )
            with lock:
                results["generated"] += 1
            if cb:
                try:
                    cb(code, "done", str(out_path))
                except Exception:
                    pass
        except Exception as e:
            LOG.error("[img:%s] 生成失败: %s", code, e)
            with lock:
                results["failed"] += 1
            if cb:
                try:
                    cb(code, "failed", "")
                except Exception:
                    pass

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=cfg.concurrency, thread_name_prefix="img"
    ) as pool:
        futures = [pool.submit(_work, c, p, o) for c, p, o in todo]
        concurrent.futures.wait(futures)

    LOG.info(
        "图片生成完成: 新增 %d / 跳过 %d / 失败 %d",
        results["generated"],
        results["skipped"],
        results["failed"],
    )
    return results


def _build_product_ref_prompt(prompts: dict, category: str = "") -> dict:
    """构建产品身份证 6 面板参考图 prompt。

    根据品类自适应展示方式:
        - 服装/鞋: 隐形人台
        - 配饰/首饰/手表: 仿真手模/耳模
        - 其他: 浮空展示
    """
    cat_lower = (category or "").lower()

    # ── 判断展示方式 ──
    is_fashion = any(kw in cat_lower for kw in (
        "女装", "男装", "服装", "连衣裙", "衬衫", "外套", "针织", "t恤",
        "运动服", "fashion", "shirt", "dress", "coat", "knit", "tshirt",
    ))
    is_shoes = any(kw in cat_lower for kw in ("鞋", "靴", "shoe"))
    is_accessory = any(kw in cat_lower for kw in (
        "配饰", "首饰", "珠宝", "手表", "耳环", "项链", "戒指", "手链",
        "jewelry", "watch", "accessor",
    ))
    is_beauty = any(kw in cat_lower for kw in (
        "美妆", "护肤", "彩妆", "香水", "beauty", "skincare", "makeup", "fragrance",
    ))

    if is_fashion or is_shoes:
        display_method = (
            "FASHION/SHOES: use invisible/ghost mannequin for front & back views, "
            "showing natural 3D body/foot shape. "
            "For shoes: invisible foot form showing toe, arch, heel shape."
        )
        top_labels = "front ghost mannequin | back ghost mannequin | side profile"
    elif is_accessory:
        display_method = (
            "ACCESSORIES: use realistic hand model (rings/bracelets), "
            "ear model (earrings), or neck/chest form (necklaces). "
            "Skin texture should look natural — NOT plastic, NOT waxy, "
            "visible pores, slight skin texture, natural warm skin tone. "
            "For watches: wrist with natural hand pose."
        )
        top_labels = "on hand/ear model front | angled view | close-up wearing detail"
    else:
        display_method = (
            "GENERAL PRODUCT: product floating slightly above white surface "
            "with soft natural drop shadow. No mannequin, no model, no stand. "
            "For beauty/skincare: show formula texture on skin or glass surface."
        )
        top_labels = "front floating view | 45° angled floating | side profile"

    # ── 下排细节面板 ──
    if is_fashion:
        detail_labels = (
            "fabric weave macro | stitching & hardware macro | label/tag detail"
        )
    elif is_accessory:
        detail_labels = (
            "material & gemstone macro | clasp/setting macro | hallmark/engraving"
        )
    elif is_beauty:
        detail_labels = (
            "formula texture macro | packaging quality | batch/label detail"
        )
    elif is_shoes:
        detail_labels = (
            "upper material macro | sole & tread macro | stitching & logo detail"
        )
    else:
        detail_labels = (
            "surface material macro | port/button/edge detail | brand label macro"
        )

    ref_text = (
        f"Product identity reference sheet. Six panels in one image. "
        f"Size 2048x1536 or larger. Clean studio background #FFFFFF. "
        f"Professional ecommerce product photography lighting 5500K. "
        f"{display_method} "
        f""
        f"TOP ROW (3 panels, equal width): {top_labels}. "
        f"CRITICAL: ALL THREE angles must be distinct — 1=front, 2=back, 3=side. "
        f"Do NOT skip the back view. Do NOT replace back with another side angle. "
        f"If the product is flat and has no meaningful back, show a reversed/rotated view. "
        f""
        f"BOTTOM ROW (3 panels, equal width): {detail_labels}. "
        f""
        f"ALL SAME PRODUCT across all 6 panels. "
        f"SAME lighting, SAME white balance, SAME exposure. "
        f"This reference will be used as the base for all subsequent "
        f"hero and detail image generation — product MUST be identical "
        f"in every panel. DO NOT change product color, shape, or material. "
        f""
        f"No text, no labels, no watermarks, no decorative elements."
    )
    return {
        "prompt": ref_text,
        "style": "professional ecommerce product reference photography",
        "style_lock": prompts.get("H1", {}).get("style_lock", ""),
        "camera": "fixed studio camera, consistent framing across all 6 panels",
        "lighting": "even studio lighting 5500K, consistent across all panels",
        "composition": f"6-panel grid, 2 rows × 3 columns, equal panels, "
                       f"top={top_labels}, bottom={detail_labels}",
        "background": "#FFFFFF clean seamless studio background",
        "color_control": ["#FFFFFF", "#2D2D2D"],
        "product_lock_rules": [
            "preserve product exactly — same shape, color, material in all panels",
        ],
        "product_ratio": "product fills 60-70% of each panel",
        "whitespace_pct": "30%",
        "is_infographic": False,
        "negative_constraints": [
            "no text, labels, watermarks",
            "no decorative elements",
            "no different products between panels",
        ],
    }


def generate_lookbook(
    client,
    cfg: Config,
    prompts: dict,
    product_image: str,
    ws: Path,
    model_attrs: str = "",
    model_scene: str = "",
    shooting_style: str = "",
    face_visible: str = "show",
    progress_callback: Any = None,
) -> dict:
    """模特套图 2 步生成流水线。

    Step 1: 生成一张「三面参考图」(正面+背面+侧面) 确立模特+产品一致性。
    Step 2: 以参考图为底图, images.edit 生成 5 张不同角度/姿势的套图。

    Args:
        progress_callback: 可选, 每完成一张图时调用 callback(code, status, path)。
        product_image: 原始产品图片路径。
        ws: SKU 工作区目录。
        model_attrs: 模特属性字符串 (种族/肤色/年龄/性别/身材)。

    Returns:
        统计 dict: {"generated": int, "skipped": int, "failed": int, "reference": str}
    """
    from ecom_image_gen.prompt_templates import LOOKBOOK_MODULES

    # 检查哪些图已存在 (断点续跑)
    todo: list[tuple[str, dict, Path]] = []
    skipped = 0
    for spec in LOOKBOOK_MODULES:
        out_path = ws / f"{spec.code}.png"
        if out_path.exists() and out_path.stat().st_size > 0 and not cfg.force:
            skipped += 1
            continue
        todo.append((spec.code, prompts.get(spec.code, {}), out_path))

    # 三面参考图
    ref_path = ws / "lookbook_ref.png"
    ref_exists = ref_path.exists() and ref_path.stat().st_size > 0 and not cfg.force

    if not todo and ref_exists:
        LOG.info("模特套图: 全部已存在, 无需生成")
        return {"generated": 0, "skipped": skipped, "failed": 0, "reference": str(ref_path)}

    # ── Step 1: 生成三面参考图 ──
    if not ref_exists:
        LOG.info("模特套图 Step 1: 生成三面参考图 (正面+背面+侧面)...")
        ref_prompt_obj = _build_reference_prompt(
            prompts, model_attrs, model_scene, shooting_style, face_visible,
        )
        try:
            generate_image(
                client, cfg, ref_prompt_obj, product_image, ref_path,
                desc="lookbook:ref",
            )
        except Exception as e:
            LOG.error("三面参考图生成失败: %s", e)
            return {"generated": 0, "skipped": skipped, "failed": len(todo), "reference": ""}
    else:
        LOG.info("模特套图 Step 1: 复用已有参考图 %s", ref_path)

    # ── Step 2: 基于参考图生成 5 张套图 ──
    if todo:
        LOG.info("模特套图 Step 2: 基于参考图生成 %d 张套图...", len(todo))
    else:
        LOG.info("模特套图: %d 张已存在, 跳过", skipped)

    results = {"generated": 0, "skipped": skipped, "failed": 0, "reference": str(ref_path)}
    lock = __import__("threading").Lock()
    cb = progress_callback

    # 已跳过的图通知前端
    if cb and skipped:
        from ecom_image_gen.prompt_templates import LOOKBOOK_MODULES as _LBM
        for spec in _LBM:
            p = ws / f"{spec.code}.png"
            if p.exists() and p.stat().st_size > 0:
                try:
                    cb(spec.code, "cached", str(p))
                except Exception:
                    pass

    def _work(code: str, prompt_obj: dict, out_path: Path) -> None:
        try:
            generate_image(
                client, cfg, prompt_obj, str(ref_path), out_path,
                desc=f"lookbook:{code}",
            )
            with lock:
                results["generated"] += 1
            if cb:
                try:
                    cb(code, "done", str(out_path))
                except Exception:
                    pass
        except Exception as e:
            LOG.error("[lookbook:%s] 生成失败: %s", code, e)
            with lock:
                results["failed"] += 1
            if cb:
                try:
                    cb(code, "failed", "")
                except Exception:
                    pass

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=cfg.concurrency, thread_name_prefix="lb"
    ) as pool:
        futures = [pool.submit(_work, c, p, o) for c, p, o in todo]
        concurrent.futures.wait(futures)

    LOG.info(
        "模特套图完成: 新增 %d / 跳过 %d / 失败 %d",
        results["generated"], results["skipped"], results["failed"],
    )
    return results


def _build_reference_prompt(
    prompts: dict,
    model_attrs: str = "",
    model_scene: str = "",
    shooting_style: str = "",
    face_visible: str = "show",
) -> dict:
    """构建三面参考图的 prompt (正面+背面+侧面模特在同一画面中)。"""
    m1 = prompts.get("M1", {})

    # 构建模特描述
    if model_attrs.strip():
        from ecom_image_gen.stage3 import _build_model_spec
        model_desc = _build_model_spec(model_attrs, "lookbook")
    else:
        model_desc = ""

    # 场景
    scene_text = ""
    if model_scene:
        scene_text = f"Scene: {model_scene}. "

    # 拍摄风格
    shoot_text = ""
    if shooting_style:
        shoot_text = f"Shooting style: {shooting_style}. "

    # 露脸
    face_text = ""
    if face_visible == "hide":
        face_text = (
            "FACE HIDDEN: Do not show the model's full face. "
            "Use back views, head crops, hair covering, or head-turned-away angles. "
        )

    ref_prompt_text = (
        "Three-view reference sheet of the same model wearing the product. "
    )
    if model_desc:
        ref_prompt_text += f"MODEL SPECIFICATION (HARD CONSTRAINT): {model_desc} "
    ref_prompt_text += (
        f"{scene_text}{shoot_text}{face_text}"
        "Layout: left section shows full front view (standing naturally, eye-level), "
        "center section shows full back view (head facing straight forward, "
        "NO looking back — neck stays neutral, no twisting, relaxed posture), "
        "right section shows 45-degree side profile. "
        "SAME model identity, SAME product, SAME lighting across all three views. "
        "Background: " + (model_scene if model_scene else "#F5F0EB clean studio") + ". "
        "Professional fashion lookbook reference photography. "
        "IMPORTANT: The model's face, body type, skin tone, ethnicity, "
        "and hair must be IDENTICAL across all three views — "
        "this reference will be used to generate consistent lookbook images. "
        "DO NOT substitute the model's ethnicity or any physical attributes. "
        "HEAD-TO-BODY RATIO: strict 1:9 nine-heads proportion, "
        "elongated legs, NO big-head-small-body, NO cartoonish Q-version proportions."
    )

    return {
        "prompt": ref_prompt_text,
        "style": m1.get("style", "fashion lookbook photography"),
        "style_lock": m1.get("style_lock", ""),
        "camera": "stationary camera, three fixed compositions in one frame",
        "lighting": m1.get("lighting", "consistent studio lighting 5500K"),
        "composition": "three-panel reference sheet, equal width per panel, "
                       "front | back | side layout",
        "background": "#F5F0EB clean studio",
        "color_control": m1.get("color_control", ["#F5F0EB", "#2D2D2D"]),
        "product_lock_rules": m1.get("product_lock_rules", [
            "preserve product exactly as in reference",
        ]),
        "product_ratio": "model+product fills 70-80% of each panel",
        "whitespace_pct": "20%",
        "is_infographic": False,
        "negative_constraints": [
            "do not change model face between views",
            "do not change product between views",
            "no different lighting per view",
        ],
    }


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _compose_edit_prompt(prompt_obj: dict) -> str:
    """把结构化 prompt 字段拼成给 images.edit 的最终英文 prompt。

    遵守 GPT-Image-2 铁律:
        1. Campaign Style Lock 首段
        2. 主 prompt 体 (含角度关键词、产品占比、留白)
        3. 技术补充: camera/lighting/composition/background
        4. 颜色: HEX 色值, 不用形容词
        5. 平台预留空间 (主图)
        6. 否定清单 (末尾)
    """
    parts: list[str] = []

    # ── 第1段: Campaign Style Lock (所有图统一) ──
    style_lock = prompt_obj.get("style_lock", "")
    if style_lock:
        parts.append(style_lock)

    # ── 第2段: 主 prompt 体 ──
    main_prompt = prompt_obj.get("prompt", "").strip()
    if main_prompt:
        parts.append(main_prompt)

    # ── 第3段: 产品占比 + 留白 ──
    ratio = prompt_obj.get("product_ratio", "")
    whitespace = prompt_obj.get("whitespace_pct", "")
    if ratio or whitespace:
        ratio_text = f"Product occupies {ratio} of frame. " if ratio else ""
        ws_text = f"Whitespace {whitespace}+. " if whitespace else ""
        parts.append(ratio_text + ws_text)

    # ── 第4段: 技术细节 ──
    if prompt_obj.get("camera"):
        parts.append(f"Camera: {prompt_obj['camera']}.")
    if prompt_obj.get("lighting"):
        parts.append(f"Lighting: {prompt_obj['lighting']}.")
    if prompt_obj.get("composition"):
        parts.append(f"Composition: {prompt_obj['composition']}.")
    if prompt_obj.get("background"):
        parts.append(f"Background: {prompt_obj['background']}.")

    # ── 第5段: 颜色 HEX (场景色调, 不改变产品色) ──
    colors = prompt_obj.get("color_control", [])
    if colors:
        parts.append(
            "Scene color palette (strict HEX, do NOT alter product color): "
            + ", ".join(colors) + "."
        )

    # ── 第5.5段: 图片内文字语言 ──
    language = prompt_obj.get("language", "")
    if language and language != "英文":
        lang_map = {
            "中文": "ALL on-image text must be in Simplified Chinese. "
                    "Wrap Chinese text with「」quotation marks. "
                    "Do NOT use English text on images.",
            "日文": "ALL on-image text must be in Japanese. "
                    "Do NOT use English or Chinese text.",
            "韩文": "ALL on-image text must be in Korean. "
                    "Do NOT use English or Chinese text.",
            "法文": "ALL on-image text must be in French. "
                    "Accents (é, è, ê, à, ç) must render correctly.",
            "西班牙文": "ALL on-image text must be in Spanish. "
                    "Accents and ñ must render correctly.",
            "德文": "ALL on-image text must be in German. "
                    "Umlauts (ä, ö, ü) and ß must render correctly.",
            "俄文": "ALL on-image text must be in Russian (Cyrillic).",
            "阿拉伯文": "ALL on-image text must be in Arabic (RTL).",
            "葡萄牙文": "ALL on-image text must be in Portuguese.",
            "意大利文": "ALL on-image text must be in Italian.",
            "泰文": "ALL on-image text must be in Thai.",
            "越南文": "ALL on-image text must be in Vietnamese.",
            "印尼文": "ALL on-image text must be in Bahasa Indonesia.",
            "土耳其文": "ALL on-image text must be in Turkish.",
        }
        if language in lang_map:
            parts.append(lang_map[language])
        else:
            parts.append(f"ALL on-image text must be in {language}. "
                         "Do NOT use English text.")

    # ── 第5.6段: 模特身份硬约束 ──
    model_attrs = prompt_obj.get("model_attrs", "")
    if model_attrs:
        from ecom_image_gen.stage3 import _build_model_spec
        try:
            spec = _build_model_spec(model_attrs, "")
            # 提取种族和性别行, 压缩为一句话
            lines = [l for l in spec.split("\n") if any(
                kw in l for kw in ("RACE/ETHNICITY:", "GENDER:", "AGE:")
            )]
            if lines:
                constraint = " ".join(l.strip() for l in lines)
                parts.append(f"MODEL IDENTITY HARD CONSTRAINT: {constraint} "
                             "DO NOT change the model's ethnicity, gender, or age. "
                             "DO NOT substitute with a different race.")
        except Exception:
            pass  # 静默失败不影响出图

    # ── 第6段: 平台预留空间 (主图 H1-H5) ──
    is_infographic = prompt_obj.get("is_infographic", False)
    if not is_infographic:
        parts.append(
            "Top center 200×100px area kept completely empty "
            "for platform price overlay."
        )

    # ── 第7段: 产品锁定规则 ──
    lock_rules = prompt_obj.get("product_lock_rules", [])
    if lock_rules:
        parts.append("CRITICAL: " + " ".join(lock_rules) + ".")

    # ── 第8段: 否定清单 (末尾) ──
    negatives = prompt_obj.get("negative_constraints", [])
    if negatives:
        neg_text = "Do NOT add: " + "; ".join(negatives) + "."
    else:
        neg_text = (
            "Do NOT add: extra props, hands, watermarks, fake logos, "
            "extra text, decorative elements, gradient backgrounds "
            "unless specified."
        )
    parts.append(neg_text)

    # ── 头身比 ──
    parts.append(
        "STRICT PROPORTION: Model head-to-body ratio 1:9 (nine-heads). "
        "Elongated legs. NO big-head-small-body, NO Q-version proportions."
    )

    # ── 最终保护指令 ──
    parts.append(
        "Keep the product exactly as in the reference image — "
        "same shape, same color, same material, same details."
    )

    return " ".join(p for p in parts if p)


def _decode_image_payload(item) -> bytes | None:
    """从 images.edit 返回的单条数据里取出图片字节 (b64_json 或 url)。"""
    # 优先 b64_json
    b64 = getattr(item, "b64_json", None)
    if b64 is None and isinstance(item, dict):
        b64 = item.get("b64_json")
    if b64:
        import base64
        return base64.b64decode(b64)

    # 其次 url (需要 requests 下载)
    url = getattr(item, "url", None)
    if url is None and isinstance(item, dict):
        url = item.get("url")
    if url:
        import requests
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        return resp.content

    return None
