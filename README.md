# PrismPix — AI 电商视觉生成引擎

输入一张产品图 + 基础信息，自动产出全套电商素材：**产品视觉分析 → 营销策略 → 14 条 GPT-Image-2 Prompt → 14 张电商图**。

采用 **images.edit API** 以原始产品图为底图，保留产品外观不变，仅合成场景/环境/背景/模特。集成 **25 个场景模板**、**Campaign Style Lock** 视觉约束和 **GPT-Image-2 生产铁律**（HEX 色值、产品占比、显式留白、否定清单、平台预留空间、多角度规则、信息图格式）。

## 架构

```
用户输入 (图片 + 类目 + 风格 + API Key)
          │
          ▼
  ┌──────────────────────────────┐
  │  Stage 1: 产品视觉分析         │  vision model 读图
  │  → product.json              │  提取材质/颜色/结构/约束
  └──────────────┬───────────────┘
                 │
                 ▼
  ┌──────────────────────────────┐
  │  Stage 2: 营销策略生成         │  text model
  │  → campaign.json             │  卖点/痛点/场景/信任元素
  └──────────────┬───────────────┘
                 │
                 ▼
  ┌──────────────────────────────────────────────┐
  │  Stage 3: Prompt 生成 (14条 H1-H5 + D1-D9)   │
  │                                              │
  │  输入: product + campaign + category + style │
  │  注入: 25场景模板 + Campaign Style Lock       │
  │        + GPT-Image-2 铁律                   │
  │  → prompts.json + prompts.md                │
  └──────────────┬───────────────────────────────┘
                 │
                 ▼
  ┌──────────────────────────────┐
  │  图片生成 (images.edit API)   │  ThreadPoolExecutor 并发
  │  → H1.png ... D9.png        │  断点续跑, 跳过已有图
  └──────────────────────────────┘
```

## 输出目录

```
output/
  SKU_NAME/
    product.json         # Stage1 — 产品结构分析
    campaign.json        # Stage2 — 营销策略
    prompts.json         # Stage3 — 14 条 prompt (可复用)
    prompts.md           # 人类可读版 (含 Style Lock + 参数表)
    prompt_cache.json    # 本地 LLM 调用缓存
    H1.png ... H5.png    # 主图 (1:1)
    D1.png ... D9.png    # 详情图 (2:3, 信息图格式)
```

## 安装

```bash
pip install -r requirements.txt       # openai + requests; flask 仅 --web 需要
cp .env.example .env                  # 填入 API Key / Base URL / 模型
```

## 用法

```bash
# 单 SKU — 完整流程 (分析→策略→prompt→出图)
python main.py --image product.jpg --sku DRESS_A001 \
  --category "女装连衣裙" --style "简约高级" --model-attrs "亚洲女性 25岁"

# 仅生成 Prompt (不出图)
python main.py --image product.jpg --sku DEMO --no-images

# 交互式输入 (不带 --image 自动进入问答)
python main.py

# Multi-SKU 批处理
python main.py --batch batch.example.json

# Web 表单 (浏览器打开 http://127.0.0.1:8000)
python main.py --web

# 强制重算 (忽略已有缓存)
python main.py --image product.jpg --sku DEMO --force --no-cache
```

### 关键参数

配置优先级: **CLI > 环境变量 > .env > 默认值**

| 参数 | 环境变量 | 说明 |
|------|---------|------|
| `--api-key` | `OPENAI_API_KEY` / `API_KEY` | API Key |
| `--base-url` | `BASE_URL` | 接入地址 (OpenAI / OneAPI / NewAPI / OpenRouter) |
| `--model` | `MODEL_NAME` | 文本/视觉模型 (需 vision 能力) |
| `--vision-model` | `VISION_MODEL` | Stage1 视觉模型 (可选) |
| `--text-model` | `TEXT_MODEL` | Stage2/3 文本模型 (可选) |
| `--image-model` | `IMAGE_MODEL` | 图像模型 (gpt-image-1/2) |
| `--output` | `OUTPUT_ROOT` | 输出根目录 (默认 `output`) |
| `--concurrency` | `CONCURRENCY` | 图片并发数 (默认 4) |
| `--max-retries` | `MAX_RETRIES` | 失败重试 (≥2, 默认 3) |
| `--force` | — | 忽略已有结果强制重算 |
| `--no-cache` | — | 禁用本地 prompt 缓存 |
| `--no-images` | — | 仅产出 prompt, 不调用图像 API |
| `--web` | — | 启动 Web 表单 (默认 http://127.0.0.1:8000) |
| `--batch` | — | 批处理 JSON 文件路径 |

## 14 个图片模块

### H1-H5 主图 (1:1 正方形, images.edit 以原图为底图)

| 模块 | 占比 | 留白 | 角度 | 景别 | 目标 |
|------|------|------|------|------|------|
| H1 | 35-40% | 45% | front 3/4 | 全景 | 纯净白底正面定妆图 |
| H2 | 25-30% | 45% | side 90° | 中景 | 45°角结构材质细节 |
| H3 | 20-25% | 50% | 仰视英雄角度 | 全景 | 模特/场景上身效果 |
| H4 | 55-60% | 40% | 90°俯视 | 微距特写 | 核心卖点视觉化特写 |
| H5 | 60-70% | 35% | rear 45° | 全景 | 多角度/配色组合陈列 |

### D1-D9 详情图 (2:3 竖版, 强制信息图格式)

每条 prompt 以 `E-commerce infographic` 开头，包含标题、图标、标签、利益点或信任徽章。

| 模块 | 占比 | 留白 | 角度 | 信息图结构 |
|------|------|------|------|-----------|
| D1 | 25-30% | 48% | front 3/4 | 首屏承接: 标题+产品+4图标+副标题 |
| D2 | 25-30% | 48% | elevated overhead 45° | 卖点1: 左产品右利益列表双栏 |
| D3 | 55-60% | 40% | macro close-up | 材质微距+标注圆+信任徽章 |
| D4 | 45-50% | 45% | side 90° | 结构拆解/爆炸图+标注 |
| D5 | 20-25% | 50% | high 45° | 场景1: 三行场景卡布局 |
| D6 | 20-25% | 50% | low angle | 场景2: 强化代入感 |
| D7 | 30-35% | 48% | front 3/4 + side | 痛点→解决对比布局 |
| D8 | 40-45% | 45% | macro close-up | 信任: 包装/质检+微距细节 |
| D9 | 30-35% | 48% | front 3/4 | CTA收尾: 标题+卖点徽章+CTA按钮 |

## 每条 Prompt 遵守的 GPT-Image-2 铁律

1. **颜色 HEX 码** — `#FFFFFF` 不写"白底"，`#D4AF37` 不写"金色"
2. **产品占比数字化** — 白底主图 35-40%, 场景图 20-25%, SKU卡 60-70%
3. **显式留白** — 主图 ≥45%, 场景 ≥50%, 详情页 ≥48%
4. **否定清单** — `Do NOT add: props, hands, watermarks, fake logos, extra text`
5. **平台预留空间** — 主图顶部中央 200×100px 留空 (价格叠加区)
6. **3 层信息架构** — 标题 Didot 28-48pt #2D2D2D + 标签 SF Pro Display 14-16pt + CTA
7. **多角度规则** — 主图 ≥3 种角度含 1 特写, 详情 ≥4 种含 2 特写, 无连续 3 张同角度
8. **详情页信息图** — 每张以 `E-commerce infographic` 开头, 含标题/图标/标签/利益点
9. **字体系统** — 标题 Didot serif, 正文 SF Pro Display sans-serif, 禁止第三种字体
10. **Campaign Style Lock** — 所有 14 张图首段统一 (色板/冷暖调/字体/背景/光线/布局)

## 工程特性

| 特性 | 说明 |
|------|------|
| **模块化架构** | 16 个 Python 模块, 依赖单向, 零循环导入 |
| **images.edit API** | 原始产品图为底图, 产品外观自动保留 |
| **25 场景模板** | 按品类+风格自适应匹配, 注入模板字段/变体/示例/anti-AI tips |
| **Campaign Style Lock** | 按品类预设色板 (8种), 14 张图视觉统一 |
| **断点续跑** | product/campaign/prompts.json 和 *.png 存在则跳过 |
| **错误重试** | 指数退避重试 (默认 3 次), 所有模型/出图调用 |
| **JSON 校验** | 鲁棒解析 (去 markdown 围栏/截取/修复尾逗号), 缺字段补默认值 |
| **并发出图** | ThreadPoolExecutor 并发生成, 可配并发数 |
| **Prompt 缓存** | 相同输入命中本地缓存, 不重复调用模型 |
| **SKU 隔离** | 每 SKU 独立工作目录, `--batch` 支持 Multi-SKU |
| **Web 表单** | Flask 浏览器上传图片触发生成 |
| **兼容多接入** | OpenAI / OneAPI / NewAPI / OpenRouter |

## 包结构

```
ecom_image_gen/
    __init__.py             # 公共 API 导出
    config.py               # Config, ProductInput, load_config()
    logging_setup.py        # 全局日志
    json_utils.py           # JSON 鲁棒解析 + Schema 校验
    cache.py                # 线程安全 PromptCache
    client.py               # OpenAI 客户端 + 重试
    image_utils.py          # 图片编码 + edits API 文件准备
    prompt_templates.py     # ModuleSpec, 14 模块定义, IRON_RULES
    template_engine.py      # 25 场景模板加载与查询
    module_template_map.py  # H1-D9 → 场景模板映射 (按品类自适应)
    style_lock.py           # Campaign Style Lock 生成
    stage1.py               # 产品视觉分析 (vision model)
    stage2.py               # 营销策略生成
    stage3.py               # Prompt 生成 (集成铁律+模板+Style Lock)
    image_gen.py            # images.edit 出图 + 并发生成
    workspace.py            # SKU 工作区 + 产物落盘
    runner.py               # 单 SKU / 批处理编排
    web.py                  # Flask Web 表单
    cli.py                  # CLI 参数 + 交互式输入
main.py                     # 入口 (96 行)
prompt_templates/           # 25 个场景模板 JSON
```

## 许可

GPL v3 License © 2026 PrismPix — 详见 [LICENSE](LICENSE) 文件。

## 安全提示

Web 服务默认监听 `127.0.0.1` (仅本机) 且无鉴权。若改用 `WEB_HOST=0.0.0.0` 暴露到网络，请自行加认证或反向代理，避免 API Key 泄露。
