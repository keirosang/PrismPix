# -*- coding: utf-8 -*-
"""
Prompt 模板引擎 — 14 模块定义 + 指令渲染 + Markdown 输出

每个模块包含:
    - 默认尺寸、产品占比、留白比例
    - 指定镜头角度和景别 (避免全套图角度雷同)
    - 详情页模块标记 is_infographic (强制信息图格式)
    - 负面约束清单

依据 GPT-Image-2 生产实战铁律 (prompt_templates/main.md)
"""

from __future__ import annotations

from dataclasses import dataclass, field

# images.edit API 支持的尺寸列表
VALID_SIZES = frozenset({
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "2048x2048",
    "2048x1152",
    "3840x2160",
    "2160x3840",
})


@dataclass
class ModuleSpec:
    """单个图片模块规格 (H1-H5 主图 / D1-D9 详情图)。

    包含 GPT-Image-2 铁律要求的:
        - product_ratio: 产品占比百分比区间
        - whitespace_pct: 显式留白百分比
        - angle: 镜头角度 (避免全套图雷同)
        - shot_size: 景别 (全景/中景/特写/微距)
        - is_infographic: 是否为信息图格式 (详情页模块)
        - negative_constraints: 该模块特定的禁止项
    """

    code: str
    group: str                          # "hero" | "detail"
    size: str                           # 默认尺寸
    objective: str                      # 业务目标
    product_ratio: str = "35-40%"       # 产品占比 (GPT-Image-2 铁律 #2)
    whitespace_pct: str = "45%"         # 显式留白 (铁律 #3)
    angle: str = "front 3/4"            # 镜头角度 (多角度规则)
    shot_size: str = "full shot"        # 景别
    is_infographic: bool = False        # 详情页强制信息图格式
    negative_constraints: list[str] = field(default_factory=lambda: [
        "no extra props, hands, or decorative objects",
        "no watermarks, fake logos, or extra text",
        "no gradient backgrounds unless specified",
        "no sci-fi or fantasy elements",
    ])


# ── 14 模块定义 (角度已按多角度规则分配, 避免连续3张同角度) ──
PROMPT_MODULES: list[ModuleSpec] = [

    # ======= H1-H5 主图 (1:1 正方形) =======
    ModuleSpec(
        "H1", "hero", "1024x1024",
        "主图: 纯净背景产品正面定妆图, 强调质感与主体, 最高点击吸引力",
        product_ratio="35-40%", whitespace_pct="45%",
        angle="front 3/4", shot_size="full shot",
    ),
    ModuleSpec(
        "H2", "hero", "1024x1024",
        "主图: 产品 45 度角展示结构与材质细节",
        product_ratio="25-30%", whitespace_pct="45%",
        angle="side 90° profile", shot_size="medium shot",
    ),
    ModuleSpec(
        "H3", "hero", "1024x1024",
        "主图: 模特/场景上身或使用状态, 体现真实尺度与穿搭/使用效果",
        product_ratio="20-25%", whitespace_pct="50%",
        angle="low angle looking up (heroic)", shot_size="full shot",
    ),
    ModuleSpec(
        "H4", "hero", "1024x1024",
        "主图: 核心卖点视觉化特写 (功能/工艺/面料)",
        product_ratio="55-60%", whitespace_pct="40%",
        angle="overhead 90° top-down", shot_size="macro close-up",
    ),
    ModuleSpec(
        "H5", "hero", "1024x1024",
        "主图: 多角度/配色组合陈列, 体现 SKU 丰富度",
        product_ratio="60-70% (整体)", whitespace_pct="35%",
        angle="rear 45° angled", shot_size="full shot",
    ),

    # ======= D1-D9 详情页 (2:3 竖版, 信息图格式) =======
    ModuleSpec(
        "D1", "detail", "1024x1536",
        "详情首屏: 大图氛围承接, 标题+产品+4个特色图标+副标题, 建立品牌调性",
        product_ratio="25-30%", whitespace_pct="48%",
        angle="front 3/4", shot_size="full shot",
        is_infographic=True,
    ),
    ModuleSpec(
        "D2", "detail", "1024x1536",
        "详情卖点1: 左产品右利益列表, 图标+短文案双栏信息图",
        product_ratio="25-30%", whitespace_pct="48%",
        angle="elevated overhead 45°", shot_size="medium shot",
        is_infographic=True,
    ),
    ModuleSpec(
        "D3", "detail", "1024x1536",
        "详情卖点2: 材质工艺微距特写+标注圆+信任徽章",
        product_ratio="55-60%", whitespace_pct="40%",
        angle="macro close-up", shot_size="macro close-up",
        is_infographic=True,
    ),
    ModuleSpec(
        "D4", "detail", "1024x1536",
        "详情结构: 拆解/爆炸图+标注, 展示内部构造或分层",
        product_ratio="45-50%", whitespace_pct="45%",
        angle="side 90° profile", shot_size="medium shot",
        is_infographic=True,
    ),
    ModuleSpec(
        "D5", "detail", "1024x1536",
        "详情场景1: 使用场景照片+场景标签, 三行场景卡布局",
        product_ratio="20-25%", whitespace_pct="50%",
        angle="high 45° looking down", shot_size="full shot",
        is_infographic=True,
    ),
    ModuleSpec(
        "D6", "detail", "1024x1536",
        "详情场景2: 另一使用场景, 强化代入感, 场景卡布局",
        product_ratio="20-25%", whitespace_pct="50%",
        angle="low angle looking up", shot_size="full shot",
        is_infographic=True,
    ),
    ModuleSpec(
        "D7", "detail", "1024x1536",
        "详情对比: 痛点→解决对比布局, 上下或左右对比信息图",
        product_ratio="30-35%", whitespace_pct="48%",
        angle="front 3/4 (before) and side profile (after)", shot_size="medium shot",
        is_infographic=True,
    ),
    ModuleSpec(
        "D8", "detail", "1024x1536",
        "详情信任: 包装/做工/质检展示+微距细节+信任徽章",
        product_ratio="40-45%", whitespace_pct="45%",
        angle="macro close-up on details", shot_size="macro close-up",
        is_infographic=True,
    ),
    ModuleSpec(
        "D9", "detail", "1024x1536",
        "详情CTA: 标题+产品+卖点徽章+CTA按钮, 转化闭合布局",
        product_ratio="30-35%", whitespace_pct="48%",
        angle="front 3/4", shot_size="full shot",
        is_infographic=True,
    ),
]

MODULE_CODES: list[str] = [m.code for m in PROMPT_MODULES]

# ── 模特套图模块 (M1-M5, generation_mode="lookbook") ──
# 2 步流程: Step1 生成三面参考图 → Step2 基于参考图生成 5 张不同角度/姿势

LOOKBOOK_MODULES: list[ModuleSpec] = [
    ModuleSpec(
        "M1", "lookbook", "1024x1024",
        "模特套图: 正面全身站立, 自然放松姿态, 眼神看向镜头",
        product_ratio="30-35%", whitespace_pct="45%",
        angle="front full body, eye-level", shot_size="full shot",
    ),
    ModuleSpec(
        "M2", "lookbook", "1024x1024",
        "模特套图: 侧身45度行走动态, 展示产品穿着时的流动感和版型",
        product_ratio="25-30%", whitespace_pct="45%",
        angle="45° side profile, walking motion", shot_size="full shot",
    ),
    ModuleSpec(
        "M3", "lookbook", "1024x1024",
        "模特套图: 纯背面或轻微侧后视角, 展示产品背部细节和穿着效果 (禁止大幅度回头)",
        product_ratio="30-35%", whitespace_pct="45%",
        angle="rear view, head straight or slight turn (<45°)", shot_size="full shot",
    ),
    ModuleSpec(
        "M4", "lookbook", "1024x1024",
        "模特套图: 半身近景, 产品与模特互动特写, 展示细节和使用方式",
        product_ratio="40-45%", whitespace_pct="40%",
        angle="medium close-up, product interaction", shot_size="medium shot",
    ),
    ModuleSpec(
        "M5", "lookbook", "1024x1536",
        "模特套图: 动态生活场景, 自然抓拍感, 展示产品在日常中的真实效果",
        product_ratio="20-25%", whitespace_pct="50%",
        angle="dynamic lifestyle, candid moment", shot_size="full shot",
        is_infographic=False,
    ),
]

LOOKBOOK_CODES: list[str] = [m.code for m in LOOKBOOK_MODULES]


def get_modules_for_mode(mode: str) -> list[ModuleSpec]:
    """根据 generation_mode 返回对应的模块列表。

    Args:
        mode: "full" | "hero" | "detail" | "lookbook"

    Returns:
        模块列表。
    """
    if mode == "hero":
        return [m for m in PROMPT_MODULES if m.group == "hero"]
    elif mode == "detail":
        return [m for m in PROMPT_MODULES if m.group == "detail"]
    elif mode == "lookbook":
        return list(LOOKBOOK_MODULES)
    else:  # "full" or default
        return list(PROMPT_MODULES)


# Stage3 prompt 对象必须包含的字段
PROMPT_FIELDS = [
    "size", "objective", "style", "prompt",
    "camera", "lighting", "composition", "background",
    "color_control", "product_lock_rules",
]

# ── GPT-Image-2 铁律 (渲染到 Stage3 指令中) ──

IRON_RULES = """
## GPT-Image-2 Prompt 铁律 (每条 Prompt 逐条遵守)

1. 颜色: 必须 HEX, 不用形容词。白底=#FFFFFF, 深灰=#2D2D2D, 金色=#D4AF37, 浅米=#F5F1E8。
2. 产品占比: 白底主图35-40%, 卖点副图25-30%, 场景图20-25%, SKU卡60-70%。
3. 留白: 主图≥45%, 场景图≥50%, 详情页≥48%。必须显式声明。
4. 否定清单: 末尾加 "Do NOT add: props, hands, watermarks, fake logos, extra text, decorations, gradient backgrounds".
5. 平台空间: 主图顶部中央200×100px留空(价格叠加区)。
6. 信息层级: 标题≤15字(#2D2D2D 28-48pt Didot), 证据2-3个(图标+标签 #7A9E7E 14-16pt SF Pro Display), CTA≤8字。中文用「」包裹。
7. 多角度: 主图≥3种角度(含1特写), 详情≥4种(含2特写), 禁连续3张同角度。角度: front 3/4 | side 90° | overhead 90° | low angle | rear 45° | macro.
8. 详情页信息图: D1-D9 以 "E-commerce infographic [type]" 开头, 含标题/图标/标签/利益点/步骤/信任徽章。
9. 字体: 标题 Didot serif, 正文 SF Pro Display sans-serif, 禁混用第三种。
10. 模特头身比: 必须 1:9 (九头身)。头长约占全身高 1/9~1/8。禁止大头娃娃、头大身小、五五身材、短腿、Q版比例。全身图拉长腿部线条。半身图头部 ≤25% 画面。特写图头部 ≤40% 画面。这是硬约束。
"""


def render_stage3_instruction() -> str:
    """渲染 Stage3 指令 (含 GPT-Image-2 铁律)。"""
    lines = [
        "你是电商商业摄影指导 + images.edit 提示词专家。",
        "系统使用 images.edit API: 原始产品图作为底图, "
        "你的 prompt 只描述场景/环境/背景/灯光/构图/模特/道具。",
        "",
        "为以下 14 个电商图模块各生成一条 prompt, "
        "返回唯一一个 JSON 对象, 顶层 key 为模块代号。",
        "",
        "模块清单 (code | 默认尺寸 | 产品占比 | 留白 | 角度 | 景别 | 目标):",
    ]
    for m in PROMPT_MODULES:
        infographic_tag = " [信息图]" if m.is_infographic else ""
        lines.append(
            f"- {m.code} | {m.size} | {m.product_ratio} | "
            f"留白{m.whitespace_pct} | {m.angle} | {m.shot_size} | "
            f"{m.objective}{infographic_tag}"
        )

    size_list = " / ".join(sorted(VALID_SIZES))
    lines += [
        "",
        "每个模块 prompt 对象必须包含:",
        f'  "size": 字符串, 可选: {size_list} (默认用清单尺寸)',
        '  "objective": 该图业务目标',
        '  "style": 固定 "ecommerce commercial photography"',
        '  "prompt": 英文, 描述期望的最终画面 (场景+模特+背景+道具+氛围+信息图布局), '
        "不需要描述产品本身 (产品来自原始图片)",
        '  "camera": 镜头/机位/焦段 (必须包含角度关键词)',
        '  "lighting": 灯光方案 (含色温如 5500K)',
        '  "composition": 构图 (含产品占比百分比和留白百分比)',
        '  "background": 背景描述 (含 HEX 色值, 详情页交替使用2-3种背景色)',
        '  "color_control": 字符串数组, HEX 色值控制场景色调, 白底=#FFFFFF',
        '  "product_lock_rules": 锁定规则',
    ]
    return "\n".join(lines)


# ── Campaign Style Lock 默认模板 ──

DEFAULT_STYLE_LOCK = (
    "Campaign Style Lock: consistent premium ecommerce visual system "
    "across the entire image set; fixed palette of clean off-white background "
    "#FFFFFF, deep charcoal text #2D2D2D, one product-matched accent color, "
    "and one soft secondary accent; neutral-cool studio lighting 5500K; "
    "modern geometric sans-serif headline placeholders only; "
    "consistent rounded rectangular info labels; consistent thin-line icon style; "
    "clean high-end product photography mixed with minimal infographic elements; "
    "stable product scale and placement; generous whitespace; "
    "no color palette changes, no mixed fonts, no random backgrounds, "
    "no inconsistent lighting, no mismatched icon styles."
)


def render_prompts_markdown(
    sku: str,
    product: dict,
    campaign: dict,
    prompts: dict,
    style_lock: str = "",
) -> str:
    """渲染人类可读的 prompts.md (含 Campaign Style Lock)。

    Args:
        sku: SKU 名称。
        product: product.json 内容。
        campaign: campaign.json 内容。
        prompts: prompts.json 内容。
        style_lock: Campaign Style Lock 文本。

    Returns:
        完整的 Markdown 字符串。
    """
    lock_text = style_lock or DEFAULT_STYLE_LOCK
    lines = [
        f"# {sku} · 电商图 Prompt 清单",
        "",
        f"- 产品: **{product.get('product_name') or '(未命名)'}**",
        f"- 类目: {product.get('category') or '-'}",
        f"- 核心卖点: {campaign.get('core_selling_point') or '-'}",
        "",
        "## Campaign Style Lock (整套图视觉合同)",
        "",
        "```text",
        lock_text,
        "```",
        "",
        "> 使用 images.edit API — 原始产品图作为底图, prompt 描述场景/环境。",
        "> 详情页模块 (D1-D9) 强制信息图格式。",
        "",
    ]

    for spec in PROMPT_MODULES:
        p = prompts.get(spec.code, {})
        group = "主图" if spec.group == "hero" else "详情图"
        info_tag = " 📊信息图" if spec.is_infographic else ""
        lines += [
            f"## {spec.code} ({group}{info_tag}) · {p.get('size', spec.size)}",
            f"> 占比: {spec.product_ratio} | 留白: {spec.whitespace_pct} | "
            f"角度: {spec.angle} | 景别: {spec.shot_size}",
            "",
            f"- **objective**: {p.get('objective', '')}",
            f"- **style**: {p.get('style', '')}",
            f"- **camera**: {p.get('camera', '')}",
            f"- **lighting**: {p.get('lighting', '')}",
            f"- **composition**: {p.get('composition', '')}",
            f"- **background**: {p.get('background', '')}",
            f"- **color_control**: {', '.join(p.get('color_control', []))}",
            f"- **product_lock_rules**: {'; '.join(p.get('product_lock_rules', []))}",
            "",
            "**prompt** (场景描述, 产品由原始图片提供):",
            "",
            "```text",
            p.get("prompt", ""),
            "```",
            "",
        ]

    return "\n".join(lines)


# ── 平台规则 (从 JSON 动态加载) ──

import json
from pathlib import Path

_METADATA_DIR = Path(__file__).resolve().parent / "metadata"


def _load_platforms() -> tuple[list[tuple[str, str]], dict[str, str]]:
    path = _METADATA_DIR / "platforms.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        options = [tuple(item) for item in data.get("options", [])]
        rules = data.get("rules", {})
        return options, rules
    except Exception as e:
        from ecom_image_gen.logging_setup import LOG
        LOG.error("无法加载 platforms.json 元数据: %s", e)
        return [], {}


def _load_languages() -> tuple[list[tuple[str, str]], dict[str, str]]:
    path = _METADATA_DIR / "languages.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        options = [tuple(item) for item in data.get("options", [])]
        rules = data.get("rules", {})
        return options, rules
    except Exception as e:
        from ecom_image_gen.logging_setup import LOG
        LOG.error("无法加载 languages.json 元数据: %s", e)
        return [], {}


PLATFORM_OPTIONS, PLATFORM_RULES = _load_platforms()
LANGUAGE_OPTIONS, LANGUAGE_RULES = _load_languages()


def get_platform_rule(platform: str) -> str:
    """获取平台规则文本。"""
    return PLATFORM_RULES.get(platform, "")


def get_language_rule(language: str) -> str:
    """获取语言规则文本。"""
    return LANGUAGE_RULES.get(language, "")
