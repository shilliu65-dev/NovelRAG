# AGENTS.md - NovelRAG 项目规则

## 0. 开工前规则

开始任何 NovelRAG 工作前，必须先读取本文件。

本项目继续遵守本地删除安全规则：

- 不使用 `del /s`
- 不使用 `rd /s`
- 不使用 `rmdir /s`
- 不使用 `Remove-Item -Recurse`
- 不使用 `rm -rf`
- 需要删除文件时，只能一次删除一个明确路径的文件。
- 如果需要批量删除文件，必须停止操作，并让用户手动删除。

## 1. 项目定位

本仓库是 `NovelRAG`，用于构建一个本地长篇中文小说知识库系统。

长期目标包括：

- 按区域检索小说内容
- 生成人物卡
- 生成人物关系表
- 建立场景和地点索引
- 查找剧情证据
- 未来提供 RAG API，供智能体、机器人或外部工具调用

当前不要一次性搭建完整系统。项目必须一步一步推进。

## 2. 核心原则

永远保留原始小说文本。

原始小说文件是事实来源。除非用户明确要求，不要覆盖、删除、移动或重写原始章节文件。

处理层级如下：

```text
原始文本 = 永久证据
清洗文本 = 处理副本
chunks = 检索单元
metadata = 导航系统
vector database = 搜索层
API = 访问层
```

不确定时，优先做保守修改，并生成报告。

## 3. 当前流水线

项目计划按这个顺序推进：

```text
小说全文
↓
章节切分
↓
区域索引
↓
清洗章节
↓
chunk 切分
↓
chunk 元数据索引
↓
向量数据库入库
↓
RAG 搜索 API
↓
人物 / 关系 / 场景 / 分镜生成
```

除非用户明确要求，不要跳步骤。

## 4. 当前阶段

小说已经完成章节切分。

当前重点是：

```text
1. 建立区域索引
2. 保护 raw chapters
3. 安全清洗文本
4. 准备带区域元数据的 chunk
5. 之后再做向量数据库
6. 之后再做 RAG API
```

不要直接跳到人物卡、关系图谱、前端 UI 或完整 RAG 系统，除非用户明确要求。

## 5. 目录约定

目标目录结构：

```text
D:\NovelRAG
├─ source\              # 原始小说全文，如果有
├─ chapters\            # 已存在的旧章节目录
├─ chapters_raw\        # 永久 raw 章节，不要编辑
├─ chapters_clean\      # 清洗后的处理章节
├─ regions\             # 区域 / 世界设定配置
├─ chunks\              # chunk markdown 文件
├─ index\               # CSV / JSON / YAML 索引
├─ vector_db\           # 本地向量数据库文件
├─ api\                 # FastAPI RAG API
├─ scripts\             # Python 脚本
├─ logs\                # 生成报告
├─ tests\               # 可选测试
├─ AGENTS.md            # 本文件
└─ requirements.txt     # Python 依赖
```

如果当前项目缺少目录，可以在相关任务中创建缺失目录。不要删除现有目录。

## 6. 文件安全规则

### 不要直接修改

不要直接修改：

```text
source/
chapters_raw/
```

如果项目当前只有 `chapters/`，下一步应先把文件复制到 `chapters_raw/`，把 `chapters_raw/` 作为永久 raw 章节。

### 可写入或更新的生成目录

允许写入或更新：

```text
chapters_clean/
chunks/
index/
logs/
vector_db/
api/
scripts/
```

### 覆盖规则

覆盖生成文件前，先判断文件中是否可能包含人工填写字段。

对于 CSV 索引文件：

- 保留已有人工填写值。
- 只新增缺失行。
- 只填充空字段。
- 不要替换用户已编辑的值，除非用户明确要求。

## 7. 区域优先设计

这部小说按区域、地点和世界范围组织，区域索引是项目的一等功能。

不要只把小说当作扁平章节序列。

系统应支持：

```text
大区域
↓
小区域
↓
场景 / 地点
↓
章节
↓
chunk
↓
人物 / 事件 / 证据
```

未来每个 chunk 都应该从所属章节或场景继承区域元数据。

## 8. 最小区域文件

需要创建并维护：

```text
regions/regions.yaml
index/chapter_region_map.csv
```

### regions/regions.yaml 格式

```yaml
regions:
  - region_id: region_001
    region_name: unknown
    parent_region_id:
    level: major
    description: unknown
    visual_style: unknown
    atmosphere: unknown
    notes: unknown

  - region_id: region_001_001
    region_name: unknown
    parent_region_id: region_001
    level: minor
    description: unknown
    visual_style: unknown
    atmosphere: unknown
    notes: unknown
```

### index/chapter_region_map.csv 字段

```text
chapter_id
chapter_order
chapter_title
major_region_id
major_region_name
minor_region_id
minor_region_name
scene_name
main_characters
summary
confidence
evidence
source_file
```

`confidence` 只能使用：

```text
high
medium
low
unknown
```

不确定时写 `unknown`。不要编造区域、剧情、人物或关系。

## 9. 未来时间线和事件索引

小说可能包含回忆、重复语句、古代历史、梦境、伏笔或倒叙。

现在不要建立完整时间线，除非用户要求。

但未来索引设计要预留这些字段：

```text
narrative_order   # 叙事顺序 / 文本出现顺序
story_order       # 故事世界真实时间顺序
time_layer        # present / past / ancient / dream / future_hint / unknown
is_flashback      # true / false / unknown
event_id          # 未来事件索引引用
```

关键区别：

```text
chapter order = 叙事顺序
story order = 故事世界真实时间
```

不要自动合并重复文本。重复语句可能是伏笔或回调。

## 10. 文本清洗规则

清洗应减少噪声，但不能破坏故事意义。

### 必须保留

始终保留：

```text
人物名
地点名
势力名
章节标题
对话
特殊术语
能力 / 系统名称
武器 / 物品
可能属于设定的数字
可能属于设定的符号
```

除非明显是噪声，否则保留这些符号或组合：

```text
Ω
-
13
第七门
黑塔-13
```

### 谨慎删除

只删除明确噪声：

```text
多余空格
多余空行
HTML / 控制字符
网页复制痕迹
无意义的 id="xxxx" 标记
重复装饰分隔线
```

### 清洗报告

每次清洗必须生成：

```text
logs/clean_report.txt
```

报告必须包括：

```text
处理章节数
清洗前 / 清洗后字符数
每章删除比例
删除比例超过 10% 的章节警告
```

如果删除比例过高，必须警告用户，不要静默继续。

## 11. Chunk 规则

chunk 必须在区域索引和文本清洗之后进行。

输入：

```text
chapters_clean/
index/chapter_region_map.csv
```

输出：

```text
chunks/
index/chunks_index.csv
logs/chunk_report.txt
```

规则：

- 优先按段落切分。
- 目标长度为 800-1200 个中文字符。
- 尽量不要在句子中间切断。
- 如果段落过长，按中文句末标点切分。
- 每个 chunk 必须保留章节和区域元数据。
- 每个 chunk 必须有稳定的 `chunk_id`。

`chunk_id` 格式：

```text
chapter_001_chunk_001
chapter_001_chunk_002
```

chunk 元数据必须包括：

```text
chunk_id
chapter_id
chapter_order
chunk_order
chapter_title
major_region_id
major_region_name
minor_region_id
minor_region_name
scene_name
main_characters
source_file
char_count
md5
```

## 12. 向量数据库规则

不要直接从 raw chapters 入库。

只能从这些输入入库：

```text
chunks/
index/chunks_index.csv
```

第一版优先使用：

```text
Chroma 本地向量数据库
collection name: novel_chunks
path: vector_db/chroma
```

每条向量记录必须包括：

```text
id = chunk_id
document = chunk 文本
metadata = chapter + region + character + source 字段
```

尽量使用 upsert，避免重复运行产生重复记录。

生成报告：

```text
logs/ingest_report.txt
```

未经用户明确同意，不要删除或重建向量数据库。

## 13. RAG API 规则

RAG API 用于未来机器人、智能体或外部工具访问。

优先框架：

```text
FastAPI
```

最小接口：

```text
GET  /health
POST /rag/search
GET  /regions
GET  /chapters
```

可选后续接口：

```text
POST /rag/ask
```

### POST /rag/search 请求

```json
{
  "query": "user question",
  "top_k": 5,
  "filters": {
    "major_region_name": "optional",
    "minor_region_name": "optional",
    "chapter_id": "optional",
    "main_characters": "optional"
  }
}
```

### POST /rag/search 响应

```json
{
  "query": "...",
  "results": [
    {
      "chunk_id": "...",
      "chapter_id": "...",
      "chapter_title": "...",
      "major_region_name": "...",
      "minor_region_name": "...",
      "main_characters": "...",
      "text": "...",
      "score": 0.0
    }
  ]
}
```

RAG 回答必须基于证据。不要编造 retrieved chunks 中不存在的内容。

## 14. 脚本要求

所有脚本都应能从项目根目录运行：

```powershell
cd D:\NovelRAG
python scripts\script_name.py
```

每个脚本都应该：

- 明确输入和输出路径。
- 创建缺失的输出目录。
- 避免覆盖人工填写数据。
- 打印简短完成摘要。
- 在 `logs/` 中生成报告。
- 必要输入缺失时，用清楚错误信息失败。

推荐脚本：

```text
scripts/init_region_index.py
scripts/clean_chapters.py
scripts/chunk_chapters.py
scripts/ingest_vector.py
api/rag_api.py
```

## 15. 依赖规则

优先使用本地 Python 环境。

如果还没有标准环境，可以创建：

```text
.venv/
requirements.txt
```

可能需要的依赖：

```text
fastapi
uvicorn
pydantic
pyyaml
pandas
chromadb
openai
python-dotenv
```

不要添加不必要的重型框架。

除非用户明确要求，不要添加前端依赖。

## 16. 暂时不要做的事

除非用户明确要求，不要做：

- 前端 UI
- 人物卡
- 关系图谱
- Neo4j 图数据库
- 复杂多智能体编排
- 重写小说
- 总结或删除 raw chapters
- 自动合并重复场景
- 自动移除重复短语
- 直接编辑 `chapters_raw/`
- 覆盖用户填写过的 CSV 字段

## 17. 不确定信息处理

如果无法从文本确定信息，写：

```text
unknown
```

`confidence` 字段只能使用：

```text
high
medium
low
unknown
```

不要编造区域名、人物名、事件或关系。

不确定时，保留原文并在报告中给出警告。

## 18. 证据规则

任何 AI 辅助抽取都必须保留证据。

证据可以是：

```text
chapter_id
chunk_id
短原文引用
source_file
```

不要创建没有证据字段的结构化结论。

示例：

```text
major_region_name = 北境
evidence = chapter_003 多次出现“雪原”“北境边城”“寒冷边境”等描述
confidence = medium
```

## 19. 推荐工作顺序

当用户要求继续项目时，优先按这个顺序：

```text
1. 验证当前目录结构
2. 如果需要，把 chapters/ 复制到 chapters_raw/
3. 创建或更新 regions/regions.yaml
4. 创建或更新 index/chapter_region_map.csv
5. 清洗 chapters_clean/
6. 切分 chunks/
7. 生成 index/chunks_index.csv
8. 入库 vector_db/chroma
9. 构建 FastAPI RAG API
10. 测试带区域过滤的 /rag/search
```

前置文件不存在时，不要运行后续步骤。

## 20. 任务完成报告格式

每次任务结束后，用简短实用的格式汇报：

```text
Files created:
Files modified:
Commands run:
Outputs generated:
Warnings:
Next recommended step:
```

## 21. 沟通风格

用户偏好实用、一步一步的说明。

回复时：

- 用户用中文时，用中文回复。
- 直接说明做了什么。
- 给具体命令。
- 避免不必要的架构空话。
- 解释关键步骤为什么重要。
- 安全下一步明确时，不问多余确认。

## 22. 默认命令

从项目根目录运行相关命令：

```powershell
cd D:\NovelRAG
python scripts\init_region_index.py
python scripts\clean_chapters.py
python scripts\chunk_chapters.py
python scripts\ingest_vector.py
uvicorn api.rag_api:app --reload --host 127.0.0.1 --port 8000
```

## 23. 最终规则

把系统做成稳定流水线，不要做成一次性脚本。

每个生成产物都应该能追溯到：

```text
original chapter
chapter_id
region metadata
chunk_id
evidence
```

如果某个修改会削弱可追溯性，不要在没有用户明确同意的情况下执行。
