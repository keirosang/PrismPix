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



# ── Flask App ──
def run_web(cfg: Config, host: str = "127.0.0.1", port: int = 8000) -> None:
    try:
        from flask import Flask, jsonify, request, send_file, render_template
    except ImportError:
        raise RuntimeError("Web 模式需要 flask: pip install flask") from None

    _base = Path(__file__).resolve().parent
    app = Flask(__name__,
                template_folder=str(_base / "templates"),
                static_folder=str(_base / "static"))

    @app.get("/")
    def index():
        return render_template("index.html",
            categories=CATEGORIES,
            styles=STYLES,
            model_regions=MODEL_REGIONS,
            model_genders=MODEL_GENDERS,
            model_ages=MODEL_AGES,
            model_skin_tones=MODEL_SKIN_TONES,
            model_body_types=MODEL_BODY_TYPES,
            model_scenes=MODEL_SCENES,
            shooting_styles=SHOOTING_STYLES,
            face_options=FACE_OPTIONS,
            platforms=PLATFORM_OPTIONS,
            languages=LANGUAGE_OPTIONS,
        )

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
        # stop_at_stage=2 → 只跑 Stage1+2, 返回营销策略供用户确认
        stop_at = request.form.get("stop_at_stage", "")
        if stop_at == "2":
            run_cfg.generation_mode = "__stage2__"
        else:
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

    # ── Step 2: Generate Prompts (Stage3 only, from existing product+campaign) ──

    @app.post("/api/generate-prompts")
    def api_generate_prompts():
        ws = request.form.get("ws", "").strip()
        if not ws:
            return jsonify({"error": "missing ws param"}), 400
        ws_path = Path(ws)
        if not ws_path.is_absolute():
            ws_path = Path(__file__).resolve().parent.parent / ws_path
        if not ws_path.exists():
            return jsonify({"error": f"workspace not found: {ws_path}"}), 404

        product_path = ws_path / "product.json"
        campaign_path = ws_path / "campaign.json"
        if not product_path.exists() or not campaign_path.exists():
            return jsonify({"error": "product.json or campaign.json missing"}), 404

        try:
            product = json.loads(product_path.read_text(encoding="utf-8"))
            campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return jsonify({"error": "invalid JSON in workspace"}), 400

        run_cfg = dataclasses.replace(cfg)
        run_cfg.generation_mode = request.form.get("generation_mode", "full").strip() or "full"
        run_cfg.force = request.form.get("force", "0") == "1"

        # 提取所有 request 值 (后台线程不可访问 request)
        p_category = request.form.get("category", "").strip()
        p_style = request.form.get("style", "").strip()
        p_extra = request.form.get("additional_requirements", "").strip()
        p_platform = request.form.get("platform", "").strip()
        p_language = request.form.get("language", "").strip()
        p_model_attrs = _build_model_attrs(request.form)
        p_model_scene = request.form.get("model_scene", "").strip()
        p_shooting_style = request.form.get("shooting_style", "").strip()
        p_face = request.form.get("face_visible", "show").strip()
        p_sku = request.form.get("sku", "SKU").strip() or "SKU"

        task_id = _task_manager.create()

        def _run_prompts():
            try:
                from ecom_image_gen.client import build_client
                from ecom_image_gen.cache import PromptCache
                from ecom_image_gen.stage3 import stage3_generate_prompts
                from ecom_image_gen.workspace import save_outputs

                client = build_client(run_cfg)
                cache = PromptCache(
                    ws_path / "prompt_cache.json",
                    enabled=run_cfg.use_prompt_cache and not run_cfg.force,
                )

                _task_manager.update(task_id, "running")
                _task_manager.add_progress(task_id, {"stage": "stage3", "status": "running"})

                prompts = stage3_generate_prompts(
                    client, run_cfg, product, campaign,
                    category=p_category, style=p_style,
                    additional_requirements=p_extra,
                    platform=p_platform, language=p_language,
                    generation_mode=run_cfg.generation_mode,
                    model_attrs=p_model_attrs,
                    model_scene=p_model_scene,
                    shooting_style=p_shooting_style,
                    face_visible=p_face,
                    cache=cache,
                )

                _task_manager.add_progress(task_id, {"stage": "stage3", "status": "done", "count": len(prompts)})
                save_outputs(ws_path, product, campaign, prompts, p_sku)
                _task_manager.update(task_id, "done", result={
                    "workspace": str(ws_path), "prompts": len(prompts),
                })
            except Exception as exc:
                LOG.exception("Prompt gen task %s failed", task_id)
                _task_manager.update(task_id, "failed", error=str(exc))

        threading.Thread(target=_run_prompts, name=f"prompt-{task_id}", daemon=True).start()
        return jsonify({"task_id": task_id, "status": "queued"})

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
        img_category = request.form.get("category", "").strip()

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
                        category=img_category,
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
