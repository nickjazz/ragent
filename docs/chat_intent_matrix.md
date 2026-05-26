# Chat Intent 處理機制設計文件

> 更新日期：2026-05-26  
> 對應實作追蹤：`docs/00_plan.md` Track T-CH、Track T-CH2

---

## 1. Intent 分類表

Intent 由輕量 LLM 呼叫（`temperature=0, max_tokens=10`）分類，單一標籤輸出。

| Intent | 說明 | 範例 |
|---|---|---|
| `GREETING` | 打招呼、道別、禮貌用語 | "你好"、"謝謝"、"掰掰" |
| `CHITCHAT` | 閒聊、情緒表達、創作（非文件依賴） | "今天心情不好"、"請寫一首詩" |
| `QUESTION` | 需從文件查找事實的問句 | "XXX 的退款政策是什麼？" |
| `SUMMARY` | 摘要文件內容 | "幫我總結這份合約" |
| `GENERATION` | **以文件為依據**草擬文字 | "根據合約條款，幫我寫一份回覆" |

> **GENERATION 定義邊界**：必須依賴文件的內容才能完成。開放式創作（詩、故事、笑話）→ 歸類為 `CHITCHAT`。

---

## 2. `context_mode` × `intent` 完整矩陣

`context_mode` 由 caller 在 request body 指定（預設 `"auto"`）。

| context_mode | intent | retrieval 是否執行 | inject\_context | system prompt | temperature (auto) | sources 回傳值 | [N] citation |
|---|---|:---:|:---:|---|:---:|---|:---:|
| `auto` | GREETING | ❌ | `False` | `_PLAIN_ASSISTANT` | 0.8 | `[]` | ❌ |
| `auto` | CHITCHAT | ❌ | `False` | `_PLAIN_ASSISTANT` | 0.8 | `[]` | ❌ |
| `auto` | QUESTION | ✅ | `True` | `_DEFAULT_RAG` | 0.2 | `[{...}]` or `null` | ✅ |
| `auto` | SUMMARY | ✅ | `True` | `_DEFAULT_RAG` | 0.2 | `[{...}]` or `null` | ✅ |
| `auto` | GENERATION | ✅ | `True` | `_DEFAULT_RAG` | 0.7 | `[{...}]` or `null` | ✅ |
| `caller` | GREETING | ❌ | `False` | `_PLAIN_ASSISTANT` | 0.8 | `[]` | ❌ |
| `caller` | CHITCHAT | ❌ | `False` | `_PLAIN_ASSISTANT` | 0.8 | `[]` | ❌ |
| `caller` | QUESTION | ❌ | `False` | `_RAG_NO_CITATION` | 0.2 | `[]` | ❌ |
| `caller` | SUMMARY | ❌ | `False` | `_RAG_NO_CITATION` | 0.2 | `[]` | ❌ |
| `caller` | GENERATION | ❌ | `False` | `_RAG_NO_CITATION` | 0.7 | `[]` | ❌ |
| `force` | GREETING | ✅ | `True` | `_DEFAULT_RAG` | 0.8 | `[{...}]` or `null` | ✅ |
| `force` | CHITCHAT | ✅ | `True` | `_DEFAULT_RAG` | 0.8 | `[{...}]` or `null` | ✅ |
| `force` | QUESTION | ✅ | `True` | `_DEFAULT_RAG` | 0.2 | `[{...}]` or `null` | ✅ |
| `force` | SUMMARY | ✅ | `True` | `_DEFAULT_RAG` | 0.2 | `[{...}]` or `null` | ✅ |
| `force` | GENERATION | ✅ | `True` | `_DEFAULT_RAG` | 0.7 | `[{...}]` or `null` | ✅ |

### sources 值語意

| 值 | 語意 |
|---|---|
| `[{...}]` | retrieval 執行且有命中文件 |
| `null` | retrieval 執行但無命中（`_build_sources` returns None when docs=[]） |
| `[]` | retrieval 被跳過（`context_mode=caller` 或 intent=GREETING/CHITCHAT） |

---

## 3. context_mode 語意

| context_mode | 說明 | 使用時機 |
|---|---|---|
| `auto` | 由 intent 決定是否 retrieve（預設） | 一般問答、多輪對話 |
| `caller` | 永遠跳過 retrieval；caller 在 user message 自帶 `<context>…</context>` | 前端自行檢索後送進來；外部系統代入上下文 |
| `force` | 永遠執行 retrieval，無論 intent 為何 | 除錯、確保 RAG grounding 的特殊場景 |

---

## 4. System Prompt 三種常數

| 常數 | 適用條件 | 有無 [N] citation 規則 | 有無 grounding 規則 |
|---|---|:---:|:---:|
| `_PLAIN_ASSISTANT_PROMPT` | `inject_context=False` + GREETING/CHITCHAT | ❌ | ❌ |
| `_DEFAULT_RAG_SYSTEM_PROMPT` | `inject_context=True` | ✅ | ✅ |
| `_RAG_GROUNDING_NO_CITATION` | `inject_context=False` + QUESTION/SUMMARY/GENERATION | ❌ | ✅ |

> **選擇邏輯**（`build_rag_messages`）：
> ```
> if intent in {GREETING, CHITCHAT} and not inject_context → _PLAIN_ASSISTANT_PROMPT
> elif inject_context                                       → _DEFAULT_RAG_SYSTEM_PROMPT (或 _RAG_GROUNDING_RULES 若 caller 有 sys msg)
> else                                                      → _RAG_GROUNDING_NO_CITATION
> ```

---

## 5. Temperature 策略

`body.temperature` 為 `float | None`（預設 `None`）：
- `None` → 使用 `_INTENT_TEMPERATURE[intent]`（intent-based auto）
- `float` → 直接使用 caller 指定值（覆蓋 intent-based）

| Intent | 預設 temperature | 設計理由 |
|---|---|---|
| GREETING | 0.8 | 對話自然、帶溫度 |
| CHITCHAT | 0.8 | 閒聊需創意與變化 |
| QUESTION | 0.2 | 事實查詢需嚴謹、低發散 |
| SUMMARY | 0.2 | 摘要需忠實原文 |
| GENERATION | 0.7 | 草擬文字兼顧流暢與基礎依賴 |

---

## 6. Citation 格式強制

### 兩層保障

| 層次 | 機制 | 可靠度 |
|---|---|---|
| Prompt ban | Rule 3 明示禁用 `【N】`、`(N)`、`[#N]`，附正反範例 | ～80%（LLM 有時飄移） |
| Output post-processing | `【N】→[N]` regex normalize，在 router 回傳前執行 | 100% 確定 |

### 正規化規則

```python
import re
_CITATION_FULLWIDTH_RE = re.compile(r'【(\d+)】')

def _normalize_citations(text: str) -> str:
    """Normalize full-width citation brackets 【N】→[N]."""
    return _CITATION_FULLWIDTH_RE.sub(r'[\1]', text)
```

> `(N)` 不做自動 normalize，避免誤改正文中的合法有序清單。

---

## 7. Context 注入位置

**注入 user message**（via `_wrap_last_user()`），不注入 system message。

- System message = **instructions**（靜態，告訴 LLM 怎麼行為）
- User message = **data**（動態，每輪攜帶 retrieved docs）

此設計符合 OpenAI / Anthropic RAG cookbook 標準做法，讓模型能明確區分規則與資料。

---

## 8. Intent 偵測流程

```
POST /chat/v1
    │
    ├─ 1. Rate limit check
    │
    ├─ 2. Intent detection (永遠執行，不被 context_mode 跳過)
    │       LLM call: temperature=0, max_tokens=10
    │       → intent: GREETING | CHITCHAT | QUESTION | SUMMARY | GENERATION
    │       → fallback: QUESTION (unknown / error)
    │
    ├─ 3. Resolve skip_retrieve
    │       auto   → _INTENT_REQUIRES_RETRIEVE[intent]
    │       caller → always True (skip)
    │       force  → always False (run)
    │
    ├─ 4. Conditional retrieval
    │
    ├─ 5. build_rag_messages(inject_context, intent)
    │       → selects system prompt by (inject_context, intent)
    │
    ├─ 6. LLM main chat / stream
    │       effective_temperature = body.temperature ?? _INTENT_TEMPERATURE[intent]
    │
    ├─ 7. _normalize_citations(content)  ← 全形括號修正
    │
    └─ 8. sources = [] if skip_retrieve else _build_sources(docs)
```
