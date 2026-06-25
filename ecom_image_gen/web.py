# -*- coding: utf-8 -*-
"""
Web 三栏 SPA — 左选项 → 中提示词 → 右进度+结果

API:
    POST /api/generate        — 分析+生成 (原完整流程)
    POST /api/generate-images — 仅图片生成 (从已有 prompts)
    GET  /api/status/<id>     — 任务进度
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ecom_image_gen.config import Config, ProductInput
from ecom_image_gen.logging_setup import LOG
from ecom_image_gen.prompt_templates import LANGUAGE_OPTIONS, PLATFORM_OPTIONS
from ecom_image_gen.runner import run_sku


# ═══════════════════════════════════════════════════════════════
# Task Manager
# ═══════════════════════════════════════════════════════════════

class TaskManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: dict[str, dict] = {}

    def create(self) -> str:
        tid = uuid.uuid4().hex[:12]
        with self._lock:
            self._tasks[tid] = {
                "id": tid, "status": "queued", "progress": [],
                "result": None, "error": None, "created_at": time.time(),
            }
        return tid

    def update(self, tid: str, status: str, **kw: Any) -> None:
        with self._lock:
            t = self._tasks.get(tid)
            if t:
                t["status"] = status
                for k, v in kw.items():
                    if k == "progress":
                        t.setdefault("progress", []).append(v)
                    else:
                        t[k] = v

    def add_progress(self, tid: str, step: dict) -> None:
        with self._lock:
            t = self._tasks.get(tid)
            if t:
                t.setdefault("progress", []).append(step)

    def get(self, tid: str) -> dict | None:
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return None
            return {
                "id": t["id"], "status": t["status"],
                "progress": list(t.get("progress", [])),
                "result": t.get("result"), "error": t.get("error"),
            }

    def cleanup(self, max_age: float = 3600) -> int:
        now = time.time()
        with self._lock:
            stale = [tid for tid, t in self._tasks.items()
                     if now - t.get("created_at", 0) > max_age]
            for tid in stale:
                # 清理关联的临时目录
                tmp_dir = self._tasks[tid].get("tmp_dir", "")
                if tmp_dir and os.path.isdir(tmp_dir):
                    try:
                        import shutil
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                    except Exception:
                        pass
                del self._tasks[tid]
        return len(stale)


_task_manager = TaskManager()


# ═══════════════════════════════════════════════════════════════
# Presets
# ═══════════════════════════════════════════════════════════════

CATEGORIES = [
    ("", "—— 类目 ——"),
    ("女装/连衣裙", "女装/连衣裙"), ("女装/上衣", "女装/上衣"),
    ("女装/裤装", "女装/裤装"), ("女装/外套", "女装/外套"),
    ("男装/上衣", "男装/上衣"), ("男装/裤装", "男装/裤装"),
    ("鞋靴/运动鞋", "鞋靴/运动鞋"), ("鞋靴/皮鞋", "鞋靴/皮鞋"),
    ("包袋/手袋", "包袋/手袋"), ("配饰/首饰", "配饰/首饰"),
    ("美妆/护肤", "美妆/护肤"), ("美妆/彩妆", "美妆/彩妆"),
    ("香水", "香水"), ("数码/手机", "数码/手机"),
    ("数码/电脑", "数码/电脑"), ("家电/小家电", "家电"),
    ("食品/零食", "食品/零食"), ("食品/酒水", "食品/酒水"),
    ("家居/装饰", "家居/装饰"), ("家居/家具", "家居/家具"),
    ("运动/健身", "运动/健身"), ("母婴/用品", "母婴"),
]
STYLES = [
    ("", "—— 风格 ——"),
    ("简约 modern minimal", "简约"), ("高级 premium luxury", "高级"),
    ("清新 fresh natural", "清新"), ("街头 urban street", "街头"),
    ("复古 vintage retro", "复古"), ("运动 athletic", "运动"),
    ("科技 tech", "科技"), ("温馨 cozy warm", "温馨"),
    ("奢华 luxury", "奢华"), ("商务 business", "商务"),
]
MODEL_REGIONS = [
    ("", "不限"),
    # 中国
    ("中国华北 North China", "中国（华北）— 北方汉族，五官大气端庄，骨相硬朗立体"),
    ("中国西北 Northwest China", "中国（西北）— 轮廓分明，粗犷原生力量感，肤色健康"),
    ("中国西藏 Tibetan", "中国（西藏）— 高原红/小麦肌，眼神纯粹，原生态美感"),
    ("中国新疆 Uyghur", "中国（新疆）— 异域浓颜，高鼻深目，立体感极强"),
    ("中国广东 Cantonese", "中国（广东）— 岭南面貌，五官柔和精致，现代都市灵动"),
    ("中国江南 Jiangnan", "中国（江南）— 温婉秀气，线条柔和，古典东方美"),
    ("中国港澳 HK/Macau", "中国（港澳）— 妆容精致，干练职场精英，现代摩登"),
    ("中国台湾 Taiwanese", "中国（台湾）— 清新甜美，亲和力强，日系文青感"),
    # 日本
    ("日本偶像 J-Idol", "日本（偶像）— 元气甜美，大眼，笑容极具感染力"),
    ("日本素人 J-Natural", "日本（素人）— 自然邻家，微瑕疵美，真实生活感"),
    ("日本通勤 J-Office", "日本（通勤）— 淡雅得体，知性温柔，微熟感"),
    ("日本原宿 J-Harajuku", "日本（原宿）— 大胆前卫，亚文化个性张力"),
    # 韩国
    ("韩国偶像 K-Idol", "韩国（偶像）— 冷白皮，五官精致对称，舞台感"),
    ("韩国素人 K-Natural", "韩国（素人）— 清新自然，单眼皮/内双，气质干净"),
    ("韩国网拍 K-Model", "韩国（网拍）— 时尚灵动，日常潮流服饰展示感"),
    ("韩国极简 K-Minimal", "韩国（极简）— 清冷疏离，面部留白多，高级感"),
    # 美国
    ("美国华裔 Chinese American", "美国（华裔）— ABC风格，亚洲骨相+欧美审美，阳光自信"),
    ("美国白人 Caucasian American", "美国（白）— 金发碧眼或棕发，五官立体，主流电商审美"),
    ("美国非裔 African American", "美国（非裔）— 深邃肤色，饱满五官，街头运动感"),
    ("美国拉美 Latino American", "美国（拉美）— 小麦/古铜肌，热情奔放，曲线丰满"),
    ("美国大码 US Plus-Size", "美国（大码）— 丰满自信，面庞圆润健康，真实体态美"),
    # 东欧
    ("东欧斯拉夫 Slavic", "东欧（斯拉夫）— 冷白皮，金发碧眼，高贵精致"),
    ("东欧清冷 Ethereal East EU", "东欧（清冷）— 超模骨相，凌厉下颌线，清冷疏离"),
    ("东欧精灵 Elf-like East EU", "东欧（精灵）— 小巧灵动，空灵脆弱，童话非现实感"),
    # 西欧
    ("西欧法式 French", "西欧（法式）— 野生眉微乱卷发，不经意时髦慵懒优雅"),
    ("西欧英伦 British", "西欧（英伦）— 复古贵族感，轮廓深邃，气质内敛"),
    # 北欧/南欧
    ("北欧冷感 Nordic Cool", "北欧（冷感白皮）— 极度白皙浅色瞳孔，雌雄同体高级冷淡"),
    ("南欧意式 Italian", "南欧（意式明艳）— 浓眉大眼毛发浓密，性感热情"),
    ("南欧古典 Greek Classic", "南欧（古典雕塑）— 鼻梁高挺直达额头，完美骨相"),
    # 南亚
    ("南亚宝莱坞 Bollywood", "南亚（宝莱坞）— 深邃眼窝浓密卷发，华丽大气色彩张力强"),
    ("南亚素人 South Asian Natural", "南亚（素人）— 深色肌肤，传统古典特征，地域风情"),
    ("南亚印欧混血 Indo-European", "南亚（印欧混血）— 深邃骨相+现代妆容，高辨识度"),
    # 东南亚
    ("东南亚泰式混血 Thai Mixed", "东南亚（泰式混血）— 亚洲柔和+欧美立体，适合美妆快时尚"),
    ("东南亚热带阳光 Tropical SE Asia", "东南亚（热带阳光）— 健康深肤色，笑容感染力，海岛度假风"),
    ("东南亚越式温婉 Vietnamese", "东南亚（越式温婉）— 身材纤细，五官柔和平淡，古典温婉"),
    # 中东
    ("中东阿拉伯 Arab", "中东（阿拉伯）— 攻击性浓颜，轮廓极深，浓重眼妆神秘感"),
    ("中东波斯 Persian", "中东（波斯）— 明艳大气精致，异域风情贵气"),
    # 非洲
    ("非洲高定黑珍珠 African Couture", "非洲（高定黑珍珠）— 顶级超模，极品头身比，黝黑发亮高端时尚感"),
    ("非洲原生深肤色 African Natural", "非洲（原生深肤色）— 深色肌自然纹理，野生力量感"),
    # 南美
    ("南美巴西阳光 Brazilian Sun", "南美（巴西阳光）— 古铜肌阳光健美，运动活力表现力强"),
    ("南美哥伦比亚丰满 Colombian Curvy", "南美（哥伦比亚丰满）— 热情明艳，曲线夸张丰满，性感"),
    # 大洋洲
    ("澳洲户外健美 Aussie Fit", "澳洲（户外健美）— 金发小麦肌，冲浪运动健康感"),
    ("新西兰毛利 Maori Native", "新西兰（毛利原生）— 五官开阔，波利尼西亚民族力量感"),
]
MODEL_GENDERS = [("", "不限"), ("女性 Female", "女"), ("男性 Male", "男")]
MODEL_AGES = [("", "不限"), ("18-25", "18-25"), ("25-35", "25-35"),
    ("35-45", "35-45"), ("45-55", "45-55"), ("55+", "55+")]
MODEL_SKIN_TONES = [("", "不限"), ("白皙 Fair", "白皙"), ("自然 Natural", "自然"),
    ("小麦 Wheat", "小麦"), ("橄榄 Olive", "橄榄"), ("深色 Dark", "深色")]
MODEL_BODY_TYPES = [("", "不限"), ("纤瘦 Slim", "纤瘦"), ("标准 Average", "标准"),
    ("沙漏 Hourglass", "沙漏 (细腰丰胸翘臀)"), ("丰满 Curvy", "丰满"), ("健壮 Athletic", "健壮")]
MODEL_SCENES = [("", "—— 场景 ——"),
    ("居家 Home", "居家"), ("街头 Street", "街头"), ("办公 Office", "办公"),
    ("运动 Gym", "运动"), ("聚会 Party", "聚会"), ("户外 Nature", "户外"),
    ("咖啡厅 Café", "咖啡厅"), ("酒店 Luxury", "酒店"), ("海滩 Beach", "海滩"),
    ("城市夜景 Night", "夜景"), ("艺术空间 Gallery", "画廊"),
    ("图书馆 Library", "图书馆"), ("花园 Garden", "花园"), ("工业风 Loft", "工业风")]
SHOOTING_STYLES = [("", "—— 拍摄 ——"),
    ("棚拍 Studio", "棚拍"), ("自拍 Selfie", "自拍"), ("抓拍 Candid", "抓拍"),
    ("POV视角 POV", "POV"), ("对镜自拍 Mirror", "对镜自拍"),
    ("街拍 Street Snap", "街拍"), ("俯拍 Overhead", "俯拍"),
    ("仰拍 Low Angle", "仰拍"), ("电影感 Cinematic", "电影感")]
FACE_OPTIONS = [("show", "露脸"), ("hide", "不露脸")]


def _build_model_attrs(form: dict) -> str:
    parts = []
    for key in ("model_region", "model_gender", "model_age", "model_skin", "model_body"):
        val = form.get(key, "")
        if not val:
            continue
        if " " in val:
            eng = val.split(" ", 1)[1].strip()
        else:
            eng = val.strip()
        if eng:
            if key == "model_skin":
                eng = f"{eng} skin"
            elif key == "model_body":
                eng = f"{eng} build"
            parts.append(eng)
    return ", ".join(parts)


def _render_options(opts: list[tuple[str, str]], sel: str = "") -> str:
    return "\n".join(
        f'<option value="{v[0]}" {"selected" if v[0]==sel else ""}>{v[1]}</option>'
        for v in opts
    )


def _build_pi(form: dict, img_path: str) -> ProductInput:
    return ProductInput(
        sku=form.get("sku", "DEMO").strip() or "DEMO",
        image=img_path,
        category=form.get("category", "").strip(),
        style=form.get("style", "").strip(),
        model_attrs=_build_model_attrs(form),
        additional_requirements=form.get("additional_requirements", "").strip(),
        platform=form.get("platform", "").strip(),
        language=form.get("language", "").strip(),
        model_scene=form.get("model_scene", "").strip(),
        shooting_style=form.get("shooting_style", "").strip(),
        face_visible=form.get("face_visible", "show").strip(),
    )


# ═══════════════════════════════════════════════════════════════
# HTML Page (三栏 SPA)
# ═══════════════════════════════════════════════════════════════

def _render_page() -> str:
    return """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PrismPix</title>
<style>
*,*::before,*::after{box-sizing:border-box}
html,body{height:100%;margin:0;font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;color:#1A1A1A;background:#F0F0F0;overflow:hidden}
#app{display:grid;grid-template-columns:1fr 1fr 0.6fr;height:100vh;gap:1px;background:#D0D0D0}
.col{background:#FFF;display:flex;flex-direction:column;overflow:hidden}
.col-head{flex-shrink:0;padding:10px 14px;font-size:13px;font-weight:700;color:#555;border-bottom:1px solid #EEE;background:#FAFAFA;text-transform:uppercase;letter-spacing:.5px}
.col-body{flex:1;overflow-y:auto;padding:10px 14px}
.col-foot{flex-shrink:0;padding:10px 14px;border-top:1px solid #EEE;background:#FAFAFA}

/* ── Left: Form ── */
fieldset{border:1px solid #E8E8E8;border-radius:6px;padding:8px 10px;margin:0 0 8px}
legend{font-size:11px;font-weight:700;color:#888;padding:0 4px}
label{display:block;font-size:11px;color:#666;margin:4px 0 1px}
input,select,textarea{width:100%;padding:5px 7px;border:1px solid #DDD;border-radius:4px;font-size:12px;font-family:inherit}
textarea{resize:vertical;min-height:50px;font-size:11px}
select{appearance:none;background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 10 10'%3E%3Cpath d='M5 7L1 3h8z' fill='%23999'/%3E%3C/svg%3E") no-repeat right 6px center #FFF;padding-right:22px}
.form-row{display:flex;gap:6px}.form-row>*{flex:1}
.btn{display:block;width:100%;padding:10px;border:0;border-radius:6px;font-size:14px;font-weight:700;cursor:pointer;text-align:center}
.btn-primary{background:#1A1A1A;color:#FFF}.btn-primary:hover{background:#333}
.btn-primary:disabled{background:#999;cursor:not-allowed}
.btn-secondary{background:#F0F0F0;color:#333}.btn-secondary:hover{background:#E0E0E0}
.required::after{content:" *";color:#D00}

/* ── Middle: Prompts ── */
.prompt-card{background:#FFF;border:1px solid #EEE;border-radius:6px;margin-bottom:8px;overflow:hidden}
.prompt-card .card-head{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;background:#FAFAFA;cursor:pointer;font-size:12px;font-weight:600;user-select:none}
.prompt-card .card-head .code{color:#1A1A1A}.prompt-card .card-head .meta{color:#999;font-size:10px}
.prompt-card .card-body{display:none;padding:8px 10px;font-size:11px;line-height:1.5;color:#444;max-height:180px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;border-top:1px solid #EEE}
.prompt-card.open .card-body{display:block}
.prompt-card .card-body.editing{background:#FFFDE7;outline:2px solid #F5A623}
.prompt-card .edit-actions{display:none;gap:6px;padding:4px 10px 8px}
.prompt-card .edit-actions.show{display:flex}
.prompt-card .edit-actions button{padding:3px 10px;border-radius:4px;border:1px solid #DDD;font-size:11px;cursor:pointer}
.prompt-card .edit-actions .save-btn{background:#4CAF50;color:#FFF;border-color:#4CAF50}
.prompt-card .edit-actions .cancel-btn{background:#FFF;color:#666}
.empty-state{text-align:center;color:#CCC;padding:40px 20px;font-size:13px}
.empty-state .icon{font-size:40px;margin-bottom:10px}

/* ── Right: Results ── */
.progress-steps{list-style:none;padding:0;margin:0}
.progress-steps li{display:flex;align-items:center;gap:8px;padding:5px 0;font-size:11px}
.progress-steps .dot{width:8px;height:8px;border-radius:50%;background:#E0E0E0;flex-shrink:0}
.progress-steps .dot.running{background:#F5A623;animation:pulse 1s infinite}
.progress-steps .dot.done{background:#4CAF50}
.progress-steps .dot.error{background:#E53935}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.progress-bar{height:4px;background:#EEE;border-radius:2px;margin:8px 0;overflow:hidden}
.progress-bar .fill{height:100%;background:#1A1A1A;border-radius:2px;transition:width .3s;width:0%}
.img-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:4px}
.img-grid img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;cursor:pointer;border:1px solid #EEE;transition:transform .15s}
.img-grid img:hover{transform:scale(1.03)}
.img-item{position:relative}.img-item .label{position:absolute;bottom:2px;left:2px;background:rgba(0,0,0,.6);color:#FFF;font-size:9px;padding:1px 5px;border-radius:2px}
.img-item.ref-row{grid-column:1/-1}.img-item.ref-row img{aspect-ratio:auto;max-height:300px;object-fit:contain}

/* ── Lightbox ── */
#lightbox{display:none;position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:999;justify-content:center;align-items:center;cursor:pointer}
#lightbox.show{display:flex}
#lightbox img{max-width:95vw;max-height:95vh;object-fit:contain;border-radius:6px}
#lightbox .close{position:absolute;top:16px;right:20px;color:#FFF;font-size:32px;cursor:pointer;opacity:.7}
#lightbox .close:hover{opacity:1}
#lightbox .nav{position:absolute;top:50%;color:#FFF;font-size:36px;cursor:pointer;opacity:.6;transform:translateY(-50%);user-select:none}
#lightbox .nav:hover{opacity:1}
#lightbox .nav.prev{left:20px}#lightbox .nav.next{right:20px}

/* ── Status bar ── */
.status-msg{padding:6px 10px;border-radius:4px;font-size:11px;margin-bottom:8px}
.status-msg.info{background:#E3F2FD;color:#1565C0}
.status-msg.ok{background:#E8F5E9;color:#2E7D32}
.status-msg.err{background:#FFEBEE;color:#C62828}
.footer{text-align:center;color:#BBB;font-size:10px;padding:4px;flex-shrink:0}
</style>
</head>
<body>
<div id="app">

<!-- ═══ LEFT: Options ═══ -->
<div class="col" id="leftCol">
<div class="col-head">📋 产品信息</div>
<div class="col-body">
<fieldset><legend>基本</legend>
  <label class="required">SKU</label><input id="sku" value="DEMO">
  <label class="required">产品图片</label><input type="file" id="imageFile" accept="image/*">
</fieldset>
<fieldset><legend>属性</legend>
  <label>类目</label><select id="category">""" + _render_options(CATEGORIES) + """</select>
  <label>风格</label><select id="style">""" + _render_options(STYLES) + """</select>
</fieldset>
<fieldset><legend>平台 & 语言</legend>
  <label>平台</label><select id="platform">""" + _render_options(PLATFORM_OPTIONS) + """</select>
  <label>语言</label><select id="language">""" + _render_options(LANGUAGE_OPTIONS) + """</select>
</fieldset>
<fieldset><legend>模特</legend>
  <div class="form-row"><div><label>模特特色</label><select id="model_region">""" + _render_options(MODEL_REGIONS) + """</select></div>
  <div><label>性别</label><select id="model_gender">""" + _render_options(MODEL_GENDERS) + """</select></div></div>
  <div class="form-row"><div><label>年龄</label><select id="model_age">""" + _render_options(MODEL_AGES) + """</select></div>
  <div><label>肤色</label><select id="model_skin">""" + _render_options(MODEL_SKIN_TONES) + """</select></div>
  <div><label>身材</label><select id="model_body">""" + _render_options(MODEL_BODY_TYPES) + """</select></div></div>
  <div class="form-row"><div><label>场景</label><select id="model_scene">""" + _render_options(MODEL_SCENES) + """</select></div>
  <div><label>拍摄</label><select id="shooting_style">""" + _render_options(SHOOTING_STYLES) + """</select></div>
  <div><label>露脸</label><select id="face_visible">""" + _render_options(FACE_OPTIONS) + """</select></div></div>
</fieldset>
<fieldset><legend>模式</legend>
  <select id="generation_mode">
    <option value="full">全套 14 张</option>
    <option value="hero">主图 5 张</option>
    <option value="detail">详情 9 张</option>
    <option value="lookbook">模特套图 5 张</option>
  </select>
</fieldset>
<fieldset><legend>额外需求</legend>
  <textarea id="additional_requirements" placeholder="暖色背景 #F5F0EB / 不要人物 / ..."></textarea>
</fieldset>
</div>
<div class="col-foot">
  <label style="display:flex;align-items:center;gap:6px;font-size:11px;margin-bottom:6px;cursor:pointer">
    <input type="checkbox" id="forceRegen" style="width:auto"> 强制重新分析 (忽略缓存)
  </label>
  <button class="btn btn-primary" id="btnAnalyze" onclick="doAnalyze()">🔍 分析产品</button>
  <div class="status-msg info" style="margin-top:6px;display:none" id="leftStatus"></div>
</div>
</div>

<!-- ═══ MIDDLE: Prompts ═══ -->
<div class="col" id="midCol">
<div class="col-head">📝 提示词 <span style="font-weight:400;font-size:10px;color:#999">(双击编辑)</span></div>
<div class="col-body" id="promptList">
  <div class="empty-state"><div class="icon">📭</div>点击左侧「分析产品」<br>生成提示词后将显示在这里</div>
</div>
<div class="col-foot">
  <button class="btn btn-primary" id="btnGenerate" disabled onclick="doGenerateImages()">🖼️ 生成图片</button>
  <button class="btn btn-secondary" style="margin-top:4px" id="btnCollapseAll" onclick="collapseAll()">折叠全部</button>
</div>
</div>

<!-- ═══ RIGHT: Results ═══ -->
<div class="col" id="rightCol">
<div class="col-head">📊 进度 & 结果</div>
<div class="col-body" id="resultArea">
  <div class="empty-state"><div class="icon">🖼️</div>生成图片后将显示在这里</div>
</div>
<div class="col-foot" id="resultFoot" style="display:none">
  <button class="btn btn-secondary" onclick="downloadAll()">📥 下载全部</button>
</div>
</div>

</div>

<!-- Lightbox -->
<div id="lightbox" onclick="closeLightbox()">
  <span class="close">&times;</span>
  <span class="nav prev" onclick="event.stopPropagation();navLightbox(-1)">‹</span>
  <span class="nav next" onclick="event.stopPropagation();navLightbox(1)">›</span>
  <img id="lightboxImg" onclick="event.stopPropagation()">
</div>

<script>
// ═══════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════
let state = {
  prompts: null,       // {H1: {...}, H2: ..., ...}
  wsPath: '',          // output workspace path
  productImage: null,  // File object
  analyzeTaskId: null,
  generateTaskId: null,
  lightboxImages: [],  // [{src, label}]
  lightboxIdx: 0,
};

function getFormData() {
  const fd = new FormData();
  fd.set('sku', document.getElementById('sku').value || 'DEMO');
  if (state.productImage) fd.set('image', state.productImage);
  ['category','style','platform','language','model_region','model_gender',
   'model_age','model_skin','model_body','model_scene','shooting_style',
   'face_visible','generation_mode','additional_requirements'].forEach(id => {
    const el = document.getElementById(id);
    if (el) fd.set(id, el.value);
  });
  fd.set('gen_images', '0'); // Analyze step: no images
  fd.set('force', document.getElementById('forceRegen').checked ? '1' : '0');
  return fd;
}

// ═══════════════════════════════════════════════
// Step 1: Analyze
// ═══════════════════════════════════════════════
async function doAnalyze() {
  const fileInput = document.getElementById('imageFile');
  if (!fileInput.files[0]) { alert('请选择产品图片'); return; }
  state.productImage = fileInput.files[0];

  const btn = document.getElementById('btnAnalyze');
  btn.disabled = true; btn.textContent = '分析中...';
  setLeftStatus('info', '提交分析任务...');

  const fd = getFormData();
  try {
    const r = await fetch('/api/generate', {method:'POST',body:fd});
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    state.analyzeTaskId = data.task_id;
    pollAnalyze();
  } catch(e) {
    setLeftStatus('err', e.message);
    btn.disabled = false; btn.textContent = '🔍 分析产品';
  }
}

function pollAnalyze() {
  const tid = state.analyzeTaskId;
  if (!tid) return;
  fetch('/api/status/' + tid).then(r => r.json()).then(data => {
    if (!data || data.error) { setLeftStatus('err', data?.error||'failed'); return; }

    // Progress
    let pct = 0, msg = '';
    const prog = data.progress || [];
    prog.forEach(p => {
      if (p.stage === 'stage1') { pct = 30; msg = '视觉分析中...'; }
      if (p.stage === 'stage2') { pct = 55; msg = '营销策略生成...'; }
      if (p.stage === 'stage3' && p.status === 'running') { pct = 75; msg = '生成提示词...'; }
      if (p.stage === 'stage3' && p.status === 'done') { pct = 90; msg = '提示词生成完成'; }
    });

    if (data.status === 'done') {
      pct = 100; msg = '✓ 分析完成';
      setLeftStatus('ok', msg);
      document.getElementById('btnAnalyze').disabled = false;
      document.getElementById('btnAnalyze').textContent = '🔍 重新分析';
      state.prompts = data.result;
      state.wsPath = data.result?.workspace || '';
      renderPrompts(data.result);
      document.getElementById('btnGenerate').disabled = false;
    } else if (data.status === 'failed') {
      setLeftStatus('err', data.error || '分析失败');
      document.getElementById('btnAnalyze').disabled = false;
      document.getElementById('btnAnalyze').textContent = '🔍 分析产品';
    } else {
      setLeftStatus('info', msg + ` (${pct}%)`);
      setTimeout(pollAnalyze, 2000);
    }
  }).catch(() => setTimeout(pollAnalyze, 3000));
}

function setLeftStatus(type, msg) {
  const el = document.getElementById('leftStatus');
  el.style.display = msg ? 'block' : 'none';
  el.className = 'status-msg ' + type;
  el.textContent = msg;
}

// ═══════════════════════════════════════════════
// Step 2: Generate Images
// ═══════════════════════════════════════════════
async function doGenerateImages() {
  if (!state.prompts) { alert('请先分析产品'); return; }
  if (!state.productImage) { alert('请选择产品图片'); return; }

  const btn = document.getElementById('btnGenerate');
  btn.disabled = true; btn.textContent = '生成中...';

  document.getElementById('resultArea').innerHTML = `
    <div class="status-msg info">提交图片生成任务...</div>
    <div class="progress-bar"><div class="fill" id="genBar" style="width:5%"></div></div>
    <div id="genProgress"></div>`;

  const fd = new FormData();
  fd.set('image', state.productImage);
  fd.set('prompts', JSON.stringify(state.prompts));
  fd.set('generation_mode', document.getElementById('generation_mode').value);
  fd.set('sku', document.getElementById('sku').value || 'DEMO');
  // Pass model_attrs for lookbook mode
  ['model_region','model_gender','model_age','model_skin','model_body',
   'model_scene','shooting_style','face_visible'].forEach(id => {
    const el = document.getElementById(id);
    if (el && el.value) fd.set(id, el.value);
  });

  try {
    const r = await fetch('/api/generate-images', {method:'POST',body:fd});
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    state.generateTaskId = data.task_id;
    pollGenerate();
  } catch(e) {
    document.getElementById('resultArea').innerHTML =
      '<div class="status-msg err">' + e.message + '</div>';
    btn.disabled = false; btn.textContent = '🖼️ 生成图片';
  }
}

let renderedImageCodes = {};

function pollGenerate() {
  const tid = state.generateTaskId;
  if (!tid) return;
  fetch('/api/status/' + tid).then(r => r.json()).then(data => {
    if (!data || data.error) {
      document.getElementById('resultArea').innerHTML =
        '<div class="status-msg err">' + (data?.error||'failed') + '</div>';
      document.getElementById('btnGenerate').disabled = false;
      document.getElementById('btnGenerate').textContent = '🖼️ 生成图片';
      return;
    }

    const prog = data.progress || [];

    // ── Incremental image display ──
    let wsPath = '';
    // Extract ws from first progress event that has it
    prog.forEach(p => { if (p.ws) wsPath = p.ws; });

    prog.forEach(p => {
      if (p.stage === 'image_done' && p.code && !renderedImageCodes[p.code]) {
        renderedImageCodes[p.code] = true;
        addImageToGrid(p.code, wsPath, p.status);
      }
    });

    // ── Progress bar ──
    let pct = 5, totalDone = 0;
    prog.forEach(p => {
      if (p.stage === 'images' && p.status === 'running') { pct = 10; if (p.ws) wsPath = p.ws; }
      if (p.stage === 'image_done') totalDone++;
    });
    // Estimate: 14 for full, 5 for hero, 9 for detail, 5 for lookbook
    const modeEl = document.getElementById('generation_mode');
    const totalEst = {full:14, hero:5, detail:9, lookbook:5}[modeEl?.value] || 14;
    if (totalDone > 0) pct = 10 + (totalDone / totalEst * 85);
    document.getElementById('genBar').style.width = Math.min(pct, 95) + '%';

    try {
      if (data.status === 'done') {
        stopPoll();
        document.getElementById('genBar').style.width = '100%';
        // lookbook 模式追加三面参考图
        const modeEl2 = document.getElementById('generation_mode');
        if (modeEl2?.value === 'lookbook' && wsPath && !renderedImageCodes['lookbook_ref']) {
          renderedImageCodes['lookbook_ref'] = true;
          addImageToGrid('lookbook_ref', wsPath, 'done');
        }
        document.getElementById('resultFoot').style.display = 'block';
      } else if (data.status === 'failed') {
        stopPoll();
        document.getElementById('resultArea').innerHTML =
          '<div class="status-msg err">' + (data.error||'failed') + '</div>';
      } else {
        setTimeout(pollGenerate, 2000);
        return;  // keep polling, don't reset button
      }
    } catch(e) {
      console.error('pollGenerate error:', e);
      stopPoll();
    }
    // 无论成功/失败/异常，确保按钮还原
    document.getElementById('btnGenerate').disabled = false;
    document.getElementById('btnGenerate').textContent =
      data.status === 'done' ? '🖼️ 重新生成' : '🖼️ 生成图片';
  }).catch(() => setTimeout(pollGenerate, 3000));
}

function addImageToGrid(code, wsPath, status) {
  const area = document.getElementById('resultArea');
  // Initialize grid on first image
  if (!document.getElementById('imgGrid')) {
    const modeEl = document.getElementById('generation_mode');
    const modeLabel = {full:'全套', hero:'主图', detail:'详情', lookbook:'套图'}[modeEl?.value] || '';
    area.innerHTML = '<div class="status-msg info">🖼️ ' + modeLabel + '图片生成中...</div>' +
      '<div class="img-grid" id="imgGrid"></div>';
  }
  const grid = document.getElementById('imgGrid');
  if (!grid) return;

  const imgSrc = '/api/image?ws=' + encodeURIComponent(wsPath) + '&code=' + code;
  const div = document.createElement('div');
  div.className = 'img-item' + (code === 'lookbook_ref' ? ' ref-row' : '');
  div.onclick = function() {
    const idx = state.lightboxImages.findIndex(i => i.code === code);
    if (idx >= 0) openLightbox(idx);
  };
  const img = document.createElement('img');
  img.src = imgSrc;
  img.alt = code;
  img.loading = 'lazy';
  img.onerror = function() { this.parentElement.style.display = 'none'; };
  const label = document.createElement('span');
  label.className = 'label';
  label.textContent = (code === 'lookbook_ref' ? '📐参考图' : code) + (status === 'cached' ? ' ↻' : '');
  div.appendChild(img);
  div.appendChild(label);
  grid.appendChild(div);

  // Track for lightbox
  state.lightboxImages = state.lightboxImages || [];
  state.lightboxImages.push({code: code, src: imgSrc});
}

// Reset image tracking on new generate
const origDoGen = doGenerateImages;
doGenerateImages = function() {
  renderedImageCodes = {};
  state.lightboxImages = [];
  document.getElementById('resultArea').innerHTML = `
    <div class="status-msg info">提交图片生成任务...</div>
    <div class="progress-bar"><div class="fill" id="genBar" style="width:5%"></div></div>`;
  origDoGen();
};

// ═══════════════════════════════════════════════
// Prompt Cards
// ═══════════════════════════════════════════════

const MODULE_ORDER = ['H1','H2','H3','H4','H5','D1','D2','D3','D4','D5','D6','D7','D8','D9','M1','M2','M3','M4','M5'];

function renderPrompts(result) {
  // result might be the full run_sku result or just prompts dict
  const prompts = result?.prompts ? null : result; // if it has .prompts field it's a summary
  // Actually, after analyze, result is the run_sku summary with .images skipped
  // The prompts are embedded in the result... let me check.
  // run_sku returns {sku, workspace, product_name, prompts: count, images: {...}}
  // The actual prompts JSON is in output/SKU/prompts.json
  // For now, we need to fetch prompts.json from the workspace
  if (result?.workspace) {
    fetch('/api/prompts?ws=' + encodeURIComponent(result.workspace))
      .then(r => r.json())
      .then(prompts => renderPromptCards(prompts))
      .catch(() => {
        // Fallback: try to extract from result
        renderPromptCards(result);
      });
  } else if (typeof result === 'object') {
    renderPromptCards(result);
  }
}

function renderPromptCards(prompts) {
  if (!prompts || !Object.keys(prompts).length) return;

  // Save for later use
  state.prompts = prompts;

  let html = '';
  const codes = MODULE_ORDER.filter(c => prompts[c]);
  codes.forEach(code => {
    const p = prompts[code];
    const promptText = p.prompt || p.prompt_text || '';
    const size = p.size || '';
    const objective = p.objective || '';
    html += `<div class="prompt-card" id="card-${code}">
      <div class="card-head" onclick="toggleCard('${code}')">
        <span class="code">${code}</span>
        <span class="meta">${size} · ${objective.slice(0,30)}</span>
      </div>
      <div class="card-body" id="body-${code}" ondblclick="editPrompt('${code}')">${escHtml(promptText)}</div>
      <div class="edit-actions" id="actions-${code}">
        <button class="save-btn" onclick="savePrompt('${code}')">✓ 确认</button>
        <button class="cancel-btn" onclick="cancelEdit('${code}')">↩ 还原</button>
      </div>
    </div>`;
  });

  document.getElementById('promptList').innerHTML = html || '<div class="empty-state">无提示词</div>';
  document.getElementById('btnGenerate').disabled = false;
}

function toggleCard(code) {
  document.getElementById('card-' + code).classList.toggle('open');
}

function collapseAll() {
  document.querySelectorAll('.prompt-card').forEach(c => c.classList.remove('open'));
}

function editPrompt(code) {
  const body = document.getElementById('body-' + code);
  const actions = document.getElementById('actions-' + code);
  if (body.classList.contains('editing')) return;

  // Store original text
  body.dataset.original = body.textContent;
  body.contentEditable = 'true';
  body.classList.add('editing');
  body.focus();
  actions.classList.add('show');
}

function savePrompt(code) {
  const body = document.getElementById('body-' + code);
  const actions = document.getElementById('actions-' + code);
  body.contentEditable = 'false';
  body.classList.remove('editing');
  actions.classList.remove('show');

  // Update state
  if (state.prompts && state.prompts[code]) {
    state.prompts[code].prompt = body.textContent;
  }
}

function cancelEdit(code) {
  const body = document.getElementById('body-' + code);
  const actions = document.getElementById('actions-' + code);
  body.textContent = body.dataset.original || '';
  body.contentEditable = 'false';
  body.classList.remove('editing');
  actions.classList.remove('show');
}

function escHtml(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ═══════════════════════════════════════════════
// Results & Lightbox
// ═══════════════════════════════════════════════
function renderResults(result) {
  const ws = result?.workspace || state.wsPath || '';
  const codes = MODULE_ORDER.filter(c => {
    // Try to find images from workspace
    return true; // We'll show all possible codes
  });

  // Build image list from workspace path
  const possibleCodes = MODULE_ORDER.filter(c => state.prompts && state.prompts[c]);
  // lookbook 模式: 前置三面参考图
  const modeEl = document.getElementById('generation_mode');
  if (modeEl?.value === 'lookbook') {
    possibleCodes.unshift('lookbook_ref');
  }
  state.lightboxImages = possibleCodes.map(code => ({
    src: '/api/image?ws=' + encodeURIComponent(ws) + '&code=' + code,
    label: code === 'lookbook_ref' ? '📐参考图' : code
  }));

  let html = '<div class="status-msg ok">✓ 生成完成</div>';
  if (result?.images) {
    html += '<div style="font-size:11px;color:#666;margin-bottom:8px">' +
      '新增 ' + (result.images.generated||0) +
      ' / 跳过 ' + (result.images.skipped||0) +
      ' / 失败 ' + (result.images.failed||0) + '</div>';
  }

  html += '<div class="img-grid" id="imgGrid">';
  state.lightboxImages.forEach((img, i) => {
    html += `<div class="img-item" onclick="openLightbox(${i})">
      <img src="${img.src}" alt="${img.label}" loading="lazy" onerror="this.parentElement.style.display='none'">
      <span class="label">${img.label}</span>
    </div>`;
  });
  html += '</div>';

  document.getElementById('resultArea').innerHTML = html;
  document.getElementById('resultFoot').style.display = 'block';
}

function openLightbox(idx) {
  state.lightboxIdx = idx;
  const img = state.lightboxImages[idx];
  if (!img) return;
  document.getElementById('lightboxImg').src = img.src;
  document.getElementById('lightbox').classList.add('show');
}

function closeLightbox() {
  document.getElementById('lightbox').classList.remove('show');
}

function navLightbox(dir) {
  const idx = state.lightboxIdx + dir;
  if (idx >= 0 && idx < state.lightboxImages.length) {
    openLightbox(idx);
  }
}

document.addEventListener('keydown', e => {
  if (document.getElementById('lightbox').classList.contains('show')) {
    if (e.key === 'Escape') closeLightbox();
    if (e.key === 'ArrowLeft') navLightbox(-1);
    if (e.key === 'ArrowRight') navLightbox(1);
  }
});

function downloadAll() {
  state.lightboxImages.forEach(img => {
    const a = document.createElement('a');
    a.href = img.src; a.download = img.label + '.png'; a.click();
  });
}

// Handle file input
document.getElementById('imageFile').addEventListener('change', function() {
  state.productImage = this.files[0];
});
</script>
<div class="footer">PrismPix © 2026 · GPL v3</div>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════
# Flask App
# ═══════════════════════════════════════════════════════════════

def run_web(cfg: Config, host: str = "127.0.0.1", port: int = 8000) -> None:
    try:
        from flask import Flask, jsonify, request, send_file
    except ImportError:
        raise RuntimeError("Web 模式需要 flask: pip install flask") from None

    app = Flask(__name__)

    @app.get("/")
    def index():
        return _render_page()

    # ── Analyze (Stage1+2+3 only, no images) ──

    @app.post("/api/generate")
    def api_generate():
        f = request.files.get("image")
        if not f:
            return jsonify({"error": "未上传图片"}), 400

        tmp_dir = Path(tempfile.mkdtemp(prefix="ecom_"))
        img_path = tmp_dir / (f.filename or "upload.png")
        f.save(img_path)

        pi = _build_pi(request.form, str(img_path))

        run_cfg = dataclasses.replace(cfg)
        run_cfg.enable_generate_images = False  # Analyze only
        run_cfg.generation_mode = request.form.get("generation_mode", "full").strip() or "full"
        run_cfg.force = request.form.get("force", "0") == "1"

        task_id = _task_manager.create()

        def _run():
            try:
                def _on_progress(stage, data):
                    _task_manager.add_progress(task_id, {"stage": stage, "ts": time.time(), **data})
                    if stage == "done":
                        _task_manager.update(task_id, "done", result=data.get("result"))
                    elif stage == "start":
                        _task_manager.update(task_id, "running")
                    elif data.get("status") == "error":
                        _task_manager.update(task_id, "failed", error=data.get("error", ""))

                result = run_sku(run_cfg, pi, progress_callback=_on_progress)
                _task_manager.update(task_id, "done", result=result)
            except Exception as exc:
                LOG.exception("Task %s failed", task_id)
                _task_manager.update(task_id, "failed", error=str(exc))

        threading.Thread(target=_run, name=f"task-{task_id}", daemon=True).start()
        return jsonify({"task_id": task_id, "status": "queued"})

    # ── Generate Images Only (from existing prompts) ──

    @app.post("/api/generate-images")
    def api_generate_images():
        f = request.files.get("image")
        prompts_str = request.form.get("prompts", "{}")
        try:
            prompts = json.loads(prompts_str)
        except json.JSONDecodeError:
            return jsonify({"error": "invalid prompts JSON"}), 400

        if not f:
            return jsonify({"error": "未上传图片"}), 400

        tmp_dir = Path(tempfile.mkdtemp(prefix="ecom_img_"))
        img_path = tmp_dir / (f.filename or "upload.png")
        f.save(img_path)

        generation_mode = request.form.get("generation_mode", "full").strip() or "full"

        # Build model_attrs from form (for lookbook reference prompt)
        model_attrs = _build_model_attrs(request.form)
        model_scene = request.form.get("model_scene", "").strip()
        shooting_style = request.form.get("shooting_style", "").strip()
        face_visible = request.form.get("face_visible", "show").strip()

        # 在线程启动前提取所有 request 值 (Flask request context 在线程内不可用)
        sku = request.form.get("sku", "DEMO").strip() or "DEMO"

        run_cfg = dataclasses.replace(cfg)
        run_cfg.enable_generate_images = True
        run_cfg.generation_mode = generation_mode
        run_cfg.force = request.form.get("force", "0") == "1"

        task_id = _task_manager.create()

        def _run_images():
            try:
                from ecom_image_gen.client import build_client
                from ecom_image_gen.image_gen import generate_all_images, generate_lookbook

                client = build_client(run_cfg)
                import re
                safe = re.sub(r"[^\w.\-]+", "_", sku).strip("_") or "SKU"
                ws = Path(run_cfg.output_root) / safe
                ws.mkdir(parents=True, exist_ok=True)

                # 每张图完成的回调 → 推送到前端
                def _on_image(code: str, status: str, path: str) -> None:
                    _task_manager.add_progress(task_id, {
                        "stage": "image_done",
                        "code": code,
                        "status": status,
                        "ws": str(ws),
                    })

                _task_manager.update(task_id, "running")
                _task_manager.add_progress(task_id, {
                    "stage": "images", "status": "running", "ws": str(ws),
                })

                if generation_mode == "lookbook":
                    stats = generate_lookbook(
                        client, run_cfg, prompts, str(img_path), ws,
                        model_attrs=model_attrs,
                        model_scene=model_scene,
                        shooting_style=shooting_style,
                        face_visible=face_visible,
                        progress_callback=_on_image,
                    )
                else:
                    stats = generate_all_images(
                        client, run_cfg, prompts, str(img_path), ws,
                        progress_callback=_on_image,
                    )

                _task_manager.add_progress(task_id, {"stage": "images", "status": "done", **stats})
                _task_manager.update(task_id, "done", result={
                    "workspace": str(ws),
                    "images": stats,
                })
            except Exception as exc:
                LOG.exception("Image gen task %s failed", task_id)
                _task_manager.update(task_id, "failed", error=str(exc))

        threading.Thread(target=_run_images, name=f"img-{task_id}", daemon=True).start()
        return jsonify({"task_id": task_id, "status": "queued"})

    @app.get("/api/status/<task_id>")
    def api_status(task_id: str):
        info = _task_manager.get(task_id)
        if not info:
            return jsonify({"error": "task not found"}), 404
        return jsonify(info)

    # ── Serve prompts.json from workspace ──

    @app.get("/api/prompts")
    def api_prompts():
        ws = request.args.get("ws", "")
        if not ws:
            return jsonify({"error": "missing ws param"}), 400
        p = Path(ws)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / p
        prompts_path = p / "prompts.json"
        if not prompts_path.exists():
            return jsonify({"error": "prompts.json not found"}), 404
        return jsonify(json.loads(prompts_path.read_text(encoding="utf-8")))

    # ── Serve generated images ──

    @app.get("/api/image")
    def api_image():
        ws = request.args.get("ws", "")
        code = request.args.get("code", "H1")
        if not ws:
            return jsonify({"error": "missing ws"}), 400
        # Resolve relative paths against the project root (parent of ecom_image_gen/)
        p = Path(ws)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / p
        img_path = p / f"{code}.png"
        if not img_path.exists():
            return jsonify({"error": f"not found: {code}.png"}), 404
        return send_file(img_path, mimetype="image/png")

    # ── Periodic cleanup ──

    def _start_cleanup():
        import time as _time
        while True:
            _time.sleep(600)  # every 10 min
            n = _task_manager.cleanup(3600)
            if n:
                LOG.info("Cleanup: removed %d old tasks", n)

    threading.Thread(target=_start_cleanup, daemon=True).start()

    LOG.info("Web SPA: http://%s:%d", host, port)
    # Use threaded=True for concurrent requests (default in Flask ≥2.3)
    app.run(host=host, port=port, debug=False, threaded=True)
