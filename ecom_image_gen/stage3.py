# -*- coding: utf-8 -*-
"""
Stage 3: Prompt 生成 (H1-H5 + D1-D9)

集成 GPT-Image-2 铁律 + 25 场景模板 + Campaign Style Lock
每张图 prompt 包含: 产品占比/留白/角度/景别/负面约束/平台空间/hex色值
详情页模块(D1-D9)强制信息图格式, 以 "E-commerce infographic" 开头
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ecom_image_gen.cache import PromptCache
from ecom_image_gen.client import call_json_model, hash_key
from ecom_image_gen.config import Config
from ecom_image_gen.logging_setup import LOG
from ecom_image_gen.module_template_map import get_module_map
from ecom_image_gen.prompt_templates import (
    IRON_RULES,
    LOOKBOOK_CODES,
    LOOKBOOK_MODULES,
    MODULE_CODES,
    PROMPT_MODULES,
    VALID_SIZES,
    get_modules_for_mode,
    render_stage3_instruction,
)
from ecom_image_gen.style_lock import generate_style_lock, render_style_lock_text
from ecom_image_gen.template_engine import TemplateEngine

STAGE3_SYSTEM = (
    "你是 GPT-Image-2 提示词工程专家 + 电商商业摄影指导。"
    ""
    "核心机制: 系统使用 images.edit API — 原始产品图作为底图(image参数), "
    "你的 prompt 只描述【场景/环境/背景/灯光/构图/信息图布局/模特/道具】, "
    "产品外观由底图自动保留。"
    ""
    "=== 必须遵守的铁律 ==="
    ""
    "1. 颜色: 必须用 HEX 码, 不用形容词。白底=#FFFFFF, 金色=#D4AF37, "
    "浅米=#F5F1E8, 深灰=#2D2D2D。color_control 字段控制场景色调。"
    ""
    "2. 产品占比: prompt 中必须写明占比百分比。"
    "白底主图 35-40%, 卖点副图 25-30%, 场景图 20-25%, 多规格卡 60-70%。"
    ""
    "3. 留白: 必须显式声明。主图 ≥45%, 场景图 ≥50%, 详情页 ≥48%。"
    ""
    "4. 否定清单: 每条 prompt 末尾必须追加具体禁止项: "
    "no extra props, hands, watermarks, fake logos, extra text, "
    "decorative elements, gradient backgrounds (除非指定)。"
    ""
    "5. 平台空间: H1-H5 主图必须在 prompt 中声明 "
    "'top center 200×100px area kept empty for platform price overlay'。"
    ""
    "6. 信息层级: 如有文字, 核心承诺 ≤15字(#2D2D2D 28-48pt Didot), "
    "关键证据 2-3个(图标+标签 #7A9E7E 14-16pt SF Pro Display), "
    "行动指令 ≤8字(CTA)。中文用「」包裹。"
    ""
    "7. 多角度: 主图5张≥3种角度含1特写, 详情9张≥4种角度含2特写。"
    "禁止连续3张同角度。全景≤40%。"
    "角度必须写入 prompt: front 3/4 | side 90° | overhead 90° | "
    "low angle | rear 45° | macro close-up。"
    ""
    "8. 详情页信息图: D1-D9 每张 prompt 必须以 "
    "'E-commerce infographic [type]' 开头, "
    "包含 headline/图标/标签/利益点/步骤或信任徽章。"
    "不是单纯的产品照片! 详情页交替使用2-3种背景色防视觉疲劳。"
    ""
    "9. 字体: 标题 Didot serif, 正文 SF Pro Display sans-serif。"
    "禁止混用第三种字体。"
    ""
    "10. Campaign Style Lock: 所有14张图的首段必须包含同一段 "
    "Campaign Style Lock (色板/冷暖调/字体/背景/光线/布局/图标系统)。"
    ""
    "11. 反AI痕迹: 模特图指定具体相机(Canon EOS R5 85mm f/1.2), "
    "可见皮肤纹理(毛孔/细纹), 自然不对称表情, "
    "'NOT retouched, NOT AI-generated look'。"
    ""
    "11. 模特头身比 1:9 (九头身): 头长约全身高 1/9, "
    "禁止大头娃娃/头大身小/短腿/Q版比例, 全身图拉长腿部。"
    ""
    "12. 自然语言优于关键词堆砌。只输出一个 JSON 对象, "
    "不要解释或 markdown。"
)

# 全局模板引擎单例
_engine: Optional[TemplateEngine] = None


def _get_engine() -> TemplateEngine:
    global _engine
    if _engine is None:
        _engine = TemplateEngine()
    return _engine


def stage3_generate_prompts(
    client,
    cfg: Config,
    product: dict,
    campaign: dict,
    category: str = "",
    style: str = "",
    additional_requirements: str = "",
    platform: str = "",
    language: str = "",
    generation_mode: str = "full",
    model_attrs: str = "",
    model_scene: str = "",
    shooting_style: str = "",
    face_visible: str = "show",
    cache: Optional[PromptCache] = None,
) -> dict:
    """Stage 3: 生成结构化 images.edit prompt (按 generation_mode 决定数量和类型)。

    每条 prompt 遵守 GPT-Image-2 铁律:
        - HEX 色值、产品占比、显式留白、否定清单
        - 平台预留空间 (主图)、信息图格式 (详情页)
        - Campaign Style Lock 首段统一
        - 多角度分配, 无连续3张同角度
        - 用户的额外需求 (additional_requirements) 全程参与
        - lookbook 模式: 2步流程 (三面参考图 → 5张套图)

    Args:
        client: openai.OpenAI 实例。
        cfg: 全局配置。
        product: product.json 内容。
        campaign: campaign.json 内容。
        category: 产品类目。
        style: 产品风格。
        additional_requirements: 用户额外需求。
        platform: 投放平台。
        language: 图片文字语言。
        generation_mode: "full" | "hero" | "detail" | "lookbook"
        cache: 可选的 PromptCache。

    Returns:
        prompts dict, key 为模块代号 (full=14, hero=5, detail=9, lookbook=5)
    """
    active_modules = get_modules_for_mode(generation_mode)
    module_count = len(active_modules)
    LOG.info(
        "Stage3: 生成 %d 条 prompt (mode=%s) ...",
        module_count, generation_mode,
    )

    engine = _get_engine()

    # 1. 生成 Campaign Style Lock
    style_lock_dict = generate_style_lock(category, style, product.get("product_name", ""))
    style_lock_text = render_style_lock_text(style_lock_dict)

    # 2. 获取模块→模板映射
    module_map = get_module_map(category, style)

    # 3. 构建模板上下文
    template_context = engine.build_all_context(
        module_map,
        category=module_map.get("H1", {}).get("category_key", ""),
        style=style,
        product_name=product.get("product_name", ""),
    )

    # 4. 模块规格表 (含占比/留白/角度/景别/信息图标记)
    spec_table = _render_spec_table()

    # 5. 用户额外需求 (如果有, 作为最高优先级注入)
    extra_section = ""
    if additional_requirements.strip():
        extra_section = (
            "\n\n===== 用户额外需求 (最高优先级, 必须逐条落实) =====\n"
            + additional_requirements.strip()
            + "\n\n请将以上额外需求融入每一条 prompt 中。"
            "如果需求与默认模板冲突, 以需求为准。"
            "例如: 需求说'不要出现人物'则所有模块跳过模特; "
            "需求说'暖色调'则 color_control 和背景色全部调整为暖色 HEX。"
        )

    # 5b. 平台 & 语言规则
    platform_section = ""
    if platform:
        from ecom_image_gen.prompt_templates import get_platform_rule
        rule = get_platform_rule(platform)
        if rule:
            platform_section = (
                f"\n\n===== 平台规则 ({platform}) =====\n{rule}\n"
                "请严格遵守平台对背景色/文字/占比/预留空间的要求。"
            )
    language_section = ""
    if language:
        from ecom_image_gen.prompt_templates import get_language_rule
        rule = get_language_rule(language)
        if rule:
            language_section = (
                f"\n\n===== 图片文字语言 ({language}) =====\n{rule}\n"
                "所有图片内的文字(标题/标签/CTA)必须使用该语言。"
            )

    # 5c. 模特规格 (种族/肤色/年龄/性别/身材 + 场景 + 拍摄方式 + 露脸)
    model_section = ""
    if model_attrs.strip() or model_scene or shooting_style or face_visible:
        spec_parts = []
        if model_attrs.strip():
            spec_parts.append(_build_model_spec(model_attrs, generation_mode))
        if model_scene:
            spec_parts.append(f"SHOOTING SCENE: {model_scene}. "
                            "All model shots should be set in this scene/environment.")
        if shooting_style:
            spec_parts.append(f"SHOOTING STYLE: {shooting_style}. "
                            "Adopt this specific photography style — "
                            "match the camera angle, lens feel, and composition typical of this style.")
        if face_visible == "hide":
            spec_parts.append(
                "FACE VISIBILITY: HIDE FACE. Do NOT show the model's full face. "
                "Use cropping (neck-down), back view, hair covering, "
                "hands/objects obscuring face, or head turned away. "
                "Focus on the product and body language instead."
            )
        else:
            spec_parts.append("FACE VISIBILITY: Show full face naturally.")

        spec_text = "\n".join(spec_parts)
        model_section = (
            f"\n\n===== 模特规格 (MODEL SPECIFICATION — HARD CONSTRAINT) =====\n"
            f"{spec_text}\n"
            f"以上模特属性是硬约束。在 lookbook 模式中, 所有 M1-M5 的模特必须是同一人。"
            f"在 hero 模式中, H3 模特展示图必须使用此规格的模特。"
            f"禁止将模特替换为其他种族/肤色/性别/年龄/体型。"
        )

    # 6. 组装 user message
    lookbook_instructions = ""
    if generation_mode == "lookbook":
        # 构建场景提示
        scene_hint = ""
        if model_scene:
            scene_hint = f"所有套图场景统一为: {model_scene}。"
        shoot_hint = ""
        if shooting_style:
            shoot_hint = f"拍摄风格: {shooting_style}。"
        face_hint = ""
        if face_visible == "hide":
            face_hint = "所有套图不露全脸 (裁切/背影/遮挡)。"

        lookbook_instructions = (
            "\n\n===== 模特套图 2 步流程 (非常重要) ====="
            "\nStep 1: 先生成一张「三面参考图」— 同一模特穿着产品, "
            "正面+背面+侧面三视图在同一画面中, 确保模特面部/体型/肤色/发型完全一致。"
            "\nStep 2: 以三面参考图为底图, images.edit 生成 M1-M5 5 张不同角度/姿势的套图。"
            f"\n{scene_hint} {shoot_hint} {face_hint}"
            "\n"
            "\n=== M1-M5 姿势要求 (必须各不相同, 杜绝重复) ==="
            "\nM1: 全身正面, 自然站立但要有明确姿势变化 — "
            "如单手插兜/双手自然下垂/一手轻触产品/侧头微笑。"
            "\nM2: 半身或大半身侧面45°, 必须与 M1 姿势完全不同 — "
            "如行走中/转身瞬间/手撩头发/手拿产品展示。"
            "\nM3: 纯背面或轻微侧后视角, 与 M1/M2 形成视角对比 — "
            "如背面行走/背面抬手整理头发/纯背影/轻微侧脸(不超过45°)。"
            "禁止大幅度回头, 禁止脖子扭曲 — 保持自然放松姿态。"
            "\nM4: 近景特写, 景别与前三张完全不同 — "
            "如产品手持特写/面料细节/模特手部与产品互动/局部身体+产品。"
            "\nM5: 场景化抓拍, 融入拍摄场景 — "
            "如坐着/倚靠/蹲下/互动环境中道具/动态瞬间。"
            "\n"
            "\n=== 防重复硬约束 ==="
            "\n1. 5 张图中任意 2 张的姿势不能相似 — "
            "如果 M1 是'站立', 则 M3 不能也是'站立', 必须是坐/倚/蹲/行走。"
            "\n2. 手臂位置必须各不相同 — 不能连续 2 张都是'双手自然下垂'。"
            "\n3. 视角均匀分布 — 正面1张/侧面1张/纯背面1张/近景1张/场景1张。"
            "\n   背面图禁止大幅度回头 — 脖子保持自然放松, 头部正向前方或轻微偏转(<45°)。"
            "\n4. 5 张图中模特脸部/体型/肤色/发型必须高度一致。"
            "\n5. 产品外观必须与原始产品图完全一致。"
            "\n6. prompt 重点描述姿势/角度/场景的变化, 不改变模特和产品本身。"
        )

    output_requirements = ""
    if generation_mode == "lookbook":
        output_requirements = (
            "\n1. 返回的 JSON 顶层 key 必须是 M1, M2, M3, M4, M5。"
            "\n2. 每个 prompt 描述的是最终套图的场景/姿势/角度, 不是参考图。"
            "\n3. product_lock_rules 必须强调模特和产品的高度一致性。"
            "\n4. M1-M5 的角度和姿势必须各不相同, 覆盖正面/侧面/背面/特写/场景。"
        )
    elif generation_mode == "hero":
        output_requirements = (
            "\n1. 返回的 JSON 顶层 key 必须是 H1, H2, H3, H4, H5。"
            "\n2. 主图(H1-H5) prompt 含: Campaign Style Lock + 产品占比 + 留白 + "
            "平台预留空间 + 否定清单。"
        )
    elif generation_mode == "detail":
        output_requirements = (
            "\n1. 返回的 JSON 顶层 key 必须是 D1, D2, ..., D9。"
            "\n2. 详情图(D1-D9) prompt 以 'E-commerce infographic [type]' 开头, "
            "含: Campaign Style Lock + 信息图布局 + 标题/图标/标签/利益点 + "
            "交替背景色 + 否定清单。"
        )
    else:
        output_requirements = (
            "\n1. 返回的 JSON 顶层 key 必须是 H1-H5 + D1-D9。"
            "\n2. 主图(H1-H5) prompt 含: Campaign Style Lock + 产品占比 + 留白 + "
            "平台预留空间 + 否定清单。"
            "\n3. 详情图(D1-D9) prompt 以 'E-commerce infographic [type]' 开头, "
            "含: Campaign Style Lock + 信息图布局 + 标题/图标/标签/利益点 + "
            "交替背景色 + 否定清单。"
        )

    user_text = (
        render_stage3_instruction()
        + "\n\n===== Campaign Style Lock (必须出现在每张图 Prompt 首段) =====\n"
        + style_lock_text
        + "\n\n===== GPT-Image-2 铁律 (逐条检查) =====\n"
        + IRON_RULES
        + platform_section
        + language_section
        + model_section
        + "\n\n===== 模块规格表 (每张图的技术参数) =====\n"
        + spec_table
        + "\n\n产品分析 JSON (场景参考, 不要重新描述产品):\n"
        + json.dumps(product, ensure_ascii=False, indent=2)
        + "\n\n营销策略 JSON:\n"
        + json.dumps(campaign, ensure_ascii=False, indent=2)
        + "\n\n===== 场景模板参考 (按模块逐一匹配) =====\n"
        + template_context
        + lookbook_instructions
        + extra_section
        + "\n\n===== 输出要求 (重要) ====="
        + output_requirements
        + "\n\n通用要求:"
        + "\n- product_lock_rules 必须含: preserve original product exactly, "
        + "do not modify shape/color/material, do not add/remove parts。"
        + "\n- color_control 是场景色调 HEX, 不是产品色。"
        + "\n- composition 含占比百分比和留白百分比。"
        + "\n- 禁止照抄示例, 必须根据产品信息定制化。"
    )

    messages = [
        {"role": "system", "content": STAGE3_SYSTEM},
        {"role": "user", "content": user_text},
    ]

    data = call_json_model(
        client,
        cfg,
        cfg.text_model,
        messages,
        desc="Stage3 prompts",
        cache=cache,
        cache_key=(
            f"stage3:v7:{generation_mode}:"
            f"{hash_key(json.dumps(product, sort_keys=True), json.dumps(campaign, sort_keys=True), category, style, additional_requirements, platform, language, model_attrs, model_scene, shooting_style, face_visible)}"
        ),
    )

    # 诊断: 输出 LLM 原始返回的摘要
    raw_keys = list(data.keys()) if isinstance(data, dict) else "not-a-dict"
    raw_json_snip = json.dumps(data, ensure_ascii=False)
    if len(raw_json_snip) > 500:
        raw_json_snip = raw_json_snip[:500] + "..."
    LOG.info("Stage3 原始响应: type=%s top_keys=%s preview=%s",
             type(data).__name__, raw_keys, raw_json_snip)

    # 注入 Style Lock 到每条 prompt 的 style 字段
    prompts = _normalize_prompts(data, style_lock_text, generation_mode, language, model_attrs)
    empty_codes = [c for c, p in prompts.items() if not p.get("prompt", "").strip()]
    if empty_codes:
        LOG.warning("Stage3: %d/%d prompt 为空 (兜底已激活): %s",
                    len(empty_codes), len(prompts), empty_codes)
    LOG.info("Stage3 完成: 共 %d 条 prompt (mode=%s)", len(prompts), generation_mode)
    return prompts


def _render_spec_table() -> str:
    """渲染模块规格表 (供 LLM 参考每张图的技术参数)。"""
    lines = ["| code | group | size | ratio | whitespace | angle | shot_size | infographic |"]
    lines.append("|------|-------|------|-------|------------|-------|-----------|-------------|")
    for m in PROMPT_MODULES:
        info = "YES" if m.is_infographic else "no"
        lines.append(
            f"| {m.code} | {m.group} | {m.size} | {m.product_ratio} | "
            f"{m.whitespace_pct} | {m.angle} | {m.shot_size} | {info} |"
        )
    # 追加 lookbook 模块
    lines.append("| | | | | | | | |")
    for m in LOOKBOOK_MODULES:
        info = "YES" if m.is_infographic else "no"
        lines.append(
            f"| {m.code} | {m.group} | {m.size} | {m.product_ratio} | "
            f"{m.whitespace_pct} | {m.angle} | {m.shot_size} | {info} |"
        )
    return "\n".join(lines)


def _normalize_prompts(raw: dict, style_lock: str = "", generation_mode: str = "full", language: str = "", model_attrs: str = "") -> dict:
    """校验/补全模块 prompt, 注入 Style Lock 和铁律参数。

    Args:
        raw: 模型原始输出 JSON。
        style_lock: Campaign Style Lock 文本。
        generation_mode: "full" | "hero" | "detail" | "lookbook"

    Returns:
        规范化的 prompts dict。
    """
    active_modules = get_modules_for_mode(generation_mode)
    all_codes = [m.code for m in active_modules]

    # 解包可能的 wrapper key (LLM 常把数据包在单个顶层 key 下)
    if not any(code in raw for code in all_codes):
        # 优先匹配已知 wrapper 名
        for wrapper in ("prompts", "modules", "data", "result", "output", "content"):
            if isinstance(raw.get(wrapper), dict):
                raw = raw[wrapper]
                break
        else:
            # 兜底: 只有一个顶层 key 且值为 dict → 自动解包
            if len(raw) == 1:
                only_val = next(iter(raw.values()))
                if isinstance(only_val, dict) and any(
                    c in only_val for c in all_codes
                ):
                    raw = only_val

    out: dict[str, dict] = {}
    for spec in active_modules:
        item = raw.get(spec.code)
        if not isinstance(item, dict):
            item = {}

        size = item.get("size") or spec.size
        if size not in VALID_SIZES:
            size = spec.size

        prompt_body = (
            item.get("prompt") or item.get("prompt_text") or
            item.get("description") or ""
        ).strip()

        # 兜底: LLM 返回了空 prompt, 用已有字段拼一个最小可用的
        if not prompt_body:
            LOG.warning("[%s] LLM 返回空 prompt, raw item keys=%s values=%s",
                        spec.code, list(item.keys()),
                        {k: str(v)[:80] for k, v in item.items() if v})
            parts = []
            if item.get("camera"):
                parts.append(f"Camera: {item['camera']}.")
            if item.get("lighting"):
                parts.append(f"Lighting: {item['lighting']}.")
            if item.get("composition"):
                parts.append(f"Composition: {item['composition']}.")
            if item.get("background"):
                parts.append(f"Background: {item['background']}.")
            if item.get("objective"):
                parts.append(f"Objective: {item['objective']}.")
            if parts:
                prompt_body = " ".join(parts)
            else:
                prompt_body = (
                    f"Professional ecommerce product photography, "
                    f"{spec.angle} angle, {spec.shot_size}, "
                    f"product occupies {spec.product_ratio}, "
                    f"whitespace {spec.whitespace_pct}+."
                )

        # 详情页强制信息图格式 (非 lookbook)
        if spec.is_infographic and not prompt_body.lower().startswith("e-commerce infographic"):
            prompt_body = (
                f"E-commerce infographic "
                f"{spec.objective.split(':')[0] if ':' in spec.objective else 'detail screen'}. "
                f"{prompt_body}"
            )

        # lookbook 模式: 强调模特一致性
        if generation_mode == "lookbook":
            lock_rules = _as_list(item.get("product_lock_rules")) or [
                "preserve model identity exactly — same face, body type, skin tone, hair",
                "preserve product exactly as in the reference image",
                "only change pose, camera angle, and scene context",
            ]
        else:
            lock_rules = _as_list(item.get("product_lock_rules")) or [
                "preserve the original product exactly as shown",
                "do not modify product shape, color, or material",
                "do not add or remove any product parts",
            ]

        norm = {
            "size": size,
            "objective": item.get("objective") or spec.objective,
            "style": item.get("style") or "ecommerce commercial photography",
            "prompt": prompt_body,
            "camera": item.get("camera") or _default_camera(spec),
            "lighting": item.get("lighting") or "studio lighting 5500K, soft diffused",
            "composition": item.get("composition") or _default_composition(spec),
            "background": item.get("background") or _default_background(spec),
            "color_control": _as_list(item.get("color_control"))
            or _default_colors(spec),
            "product_lock_rules": lock_rules,
            "style_lock": style_lock,
            "language": language,
            "model_attrs": model_attrs,
            "negative_constraints": item.get("negative_constraints") or spec.negative_constraints,
            "product_ratio": spec.product_ratio,
            "whitespace_pct": spec.whitespace_pct,
            "angle": spec.angle,
            "shot_size": spec.shot_size,
            "is_infographic": spec.is_infographic,
        }

        out[spec.code] = norm

    return out


# ── 默认值生成 (基于模块规格) ──

def _default_camera(spec) -> str:
    """根据角度和景别生成默认 camera 描述。"""
    return f"Canon EOS R5, 85mm f/1.2, {spec.angle} angle, {spec.shot_size}"


def _default_composition(spec) -> str:
    """根据占比和留白生成默认 composition。"""
    return (
        f"product occupies {spec.product_ratio} of frame, "
        f"whitespace {spec.whitespace_pct}+, "
        f"{'two-column infographic layout' if spec.is_infographic else 'clean centered composition'}"
    )


def _default_background(spec) -> str:
    """生成默认背景 (详情页交替背景色)。"""
    if spec.is_infographic:
        return "#FAF7F2 clean surface, alternate with #FFFFFF and #F5F1E8 across screens"
    return "#FFFFFF clean seamless studio background"


def _default_colors(spec) -> list[str]:
    """生成默认色板。"""
    return ["#FFFFFF", "#2D2D2D", "#7A9E7E", "#F5F1E8"]


def _as_list(v: Any) -> list:
    """安全地将任意值转为字符串列表。"""
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


def _build_model_spec(model_attrs: str, mode: str) -> str:
    """将 model_attrs 字符串解析为详细的英文模特规格描述。

    从中文标签提取种族/肤色/年龄/性别/身材,
    映射为具体的英文面部特征、体型描述、反漂移约束。

    Args:
        model_attrs: 如 "East Asian, Female, 25-35, Fair skin, Slim build"
        mode: generation_mode, lookbook 模式更严格

    Returns:
        英文模特规格描述。
    """
    attrs_lower = model_attrs.lower()

    # ── 模特特色 → 面部特征映射 (从 JSON 动态加载) ──
    import json
    from pathlib import Path
    metadata_dir = Path(__file__).resolve().parent / "metadata"
    ethnicities_path = metadata_dir / "ethnicities.json"
    
    try:
        ethnicities = json.loads(ethnicities_path.read_text(encoding="utf-8"))
    except Exception as e:
        from ecom_image_gen.logging_setup import LOG
        LOG.error("无法加载 ethnicities.json: %s", e)
        ethnicities = {}

    # ── 肤色映射 ──
    _skin_tone_map = {
        "fair": "Fair skin (Fitzpatrick type I-II), light complexion with cool or warm undertones.",
        "natural": "Natural/medium skin tone (Fitzpatrick type III), balanced complexion.",
        "wheat": "Wheat/tan skin tone (Fitzpatrick type IV), warm golden-brown complexion.",
        "olive": "Olive skin tone, greenish-warm undertone.",
        "dark": "Dark/deep skin tone (Fitzpatrick type V-VI), rich brown to deep complexion.",
    }

    # ── 体型映射 ──
    _body_map = {
        "slim": "Slim/slender body type, lean build.",
        "average": "Average/regular body type, healthy normal weight.",
        "curvy": "Curvy/plus-size body type, fuller figure with defined curves.",
        "hourglass": (
            "Hourglass body type: slim defined waist, fuller bust and hips, "
            "curvaceous yet proportionate, classic feminine silhouette "
            "(waist noticeably narrower than bust and hips)."
        ),
        "athletic": "Athletic/muscular body type, toned and fit build.",
    }

    lines: list[str] = []
    
    # 种族 (按顺序匹配, 长 key 优先)
    sorted_keys = sorted(ethnicities.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in attrs_lower:
            desc = ethnicities[key]["prompt"]
            lines.append(f"RACE/ETHNICITY: {desc}")
            break

    # 性别
    if "female" in attrs_lower or "女性" in attrs_lower:
        lines.append("GENDER: Female.")
    elif "male" in attrs_lower or "男性" in attrs_lower:
        lines.append("GENDER: Male.")

    # 年龄
    import re
    age_match = re.search(r'(\d{2})[-\s]*(\d{2})?', model_attrs)
    if age_match:
        if age_match.group(2):
            lines.append(f"AGE: {age_match.group(1)}-{age_match.group(2)} years old.")
        else:
            lines.append(f"AGE: Approximately {age_match.group(1)} years old.")

    # 肤色
    for key, desc in _skin_tone_map.items():
        if key in attrs_lower:
            lines.append(f"SKIN TONE: {desc}")
            break

    # 体型
    for key, desc in _body_map.items():
        if key in attrs_lower:
            lines.append(f"BODY TYPE: {desc}")
            break

    if not lines:
        return "No specific model constraints provided."

    # 头身比硬约束 (所有模式)
    lines.append(
        "HEAD-TO-BODY RATIO: 1:9 (nine-heads proportion). "
        "Head length must be ~1/9 of total body height. "
        "DO NOT produce big-head/small-body, cartoonish Q-version, "
        "5:5 proportion, or short legs. Full-body shots: elongate legs. "
        "Half-body: head ≤25% of frame. Close-up: head ≤40% of frame."
    )

    # lookbook 模式追加硬约束
    if mode == "lookbook":
        lines.append(
            "LOOKBOOK HARD CONSTRAINT: The model identity (face, body, skin tone, "
            "hair, age appearance) must be CONSISTENT across all 5 images (M1-M5). "
            "Only pose, camera angle, and scene context may change. "
            "DO NOT change the model's ethnicity, gender, age appearance, or body type "
            "between shots. This is non-negotiable."
        )

    return "\n".join(lines)
