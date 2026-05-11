import streamlit as st
import random
import time
import json
import os
from datetime import datetime
from google import genai

# ============================================================
# 再思考AI ver4.1 - 自己検証＋会話継続
# ============================================================

client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

MEMORY_FILE = "memory.json"

# ============================================================
# 記憶システム
# ============================================================

def load_memory():
    default = {
        "graph": {},
        "last_updated": {},
        "contradiction_log": [],
        "evolution_log": [],
        "topic_weights": {},
    }
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key, value in default.items():
            if key not in data:
                data[key] = value
        return data
    return default

def save_memory(memory):
    memory["saved_at"] = datetime.now().isoformat()
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)

def decay_memory(graph, last_updated):
    now = datetime.now()
    decayed = {}
    for a, connections in graph.items():
        decayed[a] = {}
        for b, strength in connections.items():
            key = f"{a}↔{b}"
            if key in last_updated:
                last = datetime.fromisoformat(last_updated[key])
                hours = (now - last).total_seconds() / 3600
                decay = max(0.1, 1 - (hours / 240))
                new_strength = round(strength * decay, 3)
            else:
                new_strength = strength
            if new_strength > 0.05:
                decayed[a][b] = new_strength
    return decayed

def update_graph(graph, last_updated, topics, top_score):
    topics = list(topics)
    now = datetime.now().isoformat()
    for i in range(len(topics)):
        for j in range(i+1, len(topics)):
            a, b = topics[i], topics[j]
            graph.setdefault(a, {})
            graph.setdefault(b, {})
            prev = graph[a].get(b, 0)
            graph[a][b] = round(prev * 0.7 + top_score * 0.3, 3)
            graph[b][a] = graph[a][b]
            last_updated[f"{a}↔{b}"] = now
            last_updated[f"{b}↔{a}"] = now
    return graph, last_updated

# ============================================================
# コア関数
# ============================================================

def detect_purpose(text):
    short = sum(1 for w in ["すぐ","今","早く","簡単"] if w in text)
    long  = sum(1 for w in ["本質","しっかり","根本","ちゃんと"] if w in text)
    if short > long:   return {"type": "short", "weight": 0.3}
    elif long > short: return {"type": "long",  "weight": 0.8}
    else:              return {"type": "mid",   "weight": 0.5}

def score(answer, purpose_weight, graph_bonus=0, topic_bonus=0):
    return round(
        answer["confidence"] * 60 * purpose_weight
        - answer["variation"] * 0.25
        - answer["length"] * 0.1
        + graph_bonus
        + topic_bonus
    , 2)

def graph_bonus_score(graph, topics):
    total = 0
    for t in topics:
        if t in graph:
            total += sum(graph[t].values())
    return round(total * 2, 2)

def topic_bonus_score(topic_weights, topics):
    total = sum(topic_weights.get(t, 0) for t in topics)
    return round(total, 2)

def graph_pattern(graph, topics):
    hits = []
    for t in topics:
        if t in graph:
            for connected, strength in graph[t].items():
                if connected not in topics and strength > 0.3:
                    hits.append((connected, strength))
    return sorted(hits, key=lambda x: x[1], reverse=True)

def probabilistic_spark(candidates, rate=0.2):
    if len(candidates) >= 2 and random.random() < rate:
        return random.choice(candidates[1:]), True
    return candidates[0], False

def extract_topics(query):
    prompt = f"""
以下の文章から、最も重要な具体的なトピックを3つ抽出してください。

文章:「{query}」

ルール：
- 各トピックは2〜6文字の日本語
- 「一般」「理解」など曖昧な言葉は使わない
- 必ずこのJSON形式のみで返す（他の文字不要）

["トピック1", "トピック2", "トピック3"]
"""
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            text = response.text.strip()
            start = text.find("[")
            end   = text.rfind("]") + 1
            topics = json.loads(text[start:end])
            filtered = [
                t for t in topics[:3]
                if t not in ["一般", "理解", "学習", "練習", "上達"]
            ]
            if len(filtered) >= 2:
                return set(filtered)
            return set(topics[:3])
        except:
            if attempt < 2:
                time.sleep(3)
    return {"スキル習得", "反復練習"}

def generate_answers(query, purpose, conversation_history=None):
    style = {
        "short": "すぐ実践できる具体的な方法",
        "long":  "本質的な理解につながる方法",
        "mid":   "バランスのとれた方法",
    }[purpose["type"]]

    # 会話履歴を含める
    history_text = ""
    if conversation_history:
        history_text = "\n\n【これまでの会話】\n"
        for h in conversation_history[-3:]:  # 直近3件
            history_text += f"問い：{h['query']}\n"
            history_text += f"答え：{h['answer']}\n\n"

    prompt = f"""
{history_text}
「{query}」に対して、{style}で3つの異なる視点から答えてください。

必ずこのJSON形式のみで返してください（他の文字は不要）:
[
  {{"text": "答え1（100文字以内）", "confidence": 0.9}},
  {{"text": "答え2（100文字以内）", "confidence": 0.7}},
  {{"text": "答え3（100文字以内）", "confidence": 0.5}}
]
"""
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            text = response.text.strip()
            start = text.find("[")
            end   = text.rfind("]") + 1
            data  = json.loads(text[start:end])
            candidates = []
            for i, d in enumerate(data[:3]):
                candidates.append({
                    "id":         i + 1,
                    "text":       d["text"],
                    "confidence": float(d["confidence"]),
                    "variation":  round(20 + i * 20, 1),
                    "length":     round(len(d["text"]) / 10, 1),
                })
            return candidates
        except Exception as e:
            if attempt < 2:
                time.sleep(5)
    return []

# ============================================================
# 自己検証
# ============================================================

def self_verify(query, best_answer, topics):
    """
    AIが自分の答えを自分で検証する
    「この答えで十分か？」を問う
    """
    prompt = f"""
あなたはAIです。以下の問いに対して以下の答えを出しました。

問い：「{query}」
答え：「{best_answer}」
トピック：{', '.join(topics)}

この答えを自己検証してください。

必ずこのJSON形式のみで返してください：
{{
  "sufficient": true or false,
  "reason": "十分/不十分な理由（30文字以内）",
  "follow_up": "もし不十分なら追加で聞くべき質問（不十分でない場合はnull）"
}}
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        text = response.text.strip()
        start = text.find("{")
        end   = text.rfind("}") + 1
        return json.loads(text[start:end])
    except:
        return {
            "sufficient": True,
            "reason": "検証完了",
            "follow_up": None
        }

def detect_contradiction(scored, purpose_weight, graph_bonus):
    if len(scored) < 2:
        return None
    s1 = score(scored[0], purpose_weight, graph_bonus)
    s2 = score(scored[1], purpose_weight, graph_bonus)
    diff = abs(s1 - s2)
    if diff < 5:
        return {
            "候補A": scored[0]["text"][:30] + "...",
            "候補B": scored[1]["text"][:30] + "...",
            "差": round(diff, 1),
        }
    return None

def generate_questions_from_contradiction(contradiction, topics, query):
    topics_str = "・".join(list(topics)[:2])
    prompt = f"""
以下の状況で2つの答えが拮抗しています：

元の問い：「{query}」
答えA：「{contradiction['候補A']}」
答えB：「{contradiction['候補B']}」
関連トピック：{topics_str}

この矛盾の原因を探る重要な問いを1つ生成してください。

必ずこのJSON形式のみで返してください：
{{"question": "問いの内容", "reason": "なぜこの問いが重要か（30文字以内）"}}
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        text = response.text.strip()
        start = text.find("{")
        end   = text.rfind("}") + 1
        return json.loads(text[start:end])
    except:
        return {
            "question": f"なぜ「{topics_str}」で答えが割れるのか？",
            "reason": "矛盾の根本を探る"
        }

def evolve(memory, topics, best_score, contradiction):
    evolution_log = []
    topic_weights = memory.get("topic_weights", {})

    if contradiction:
        for t in topics:
            prev = topic_weights.get(t, 0)
            topic_weights[t] = round(prev - 0.05, 3)
            evolution_log.append(
                f"「{t}」重みを調整（矛盾検出）: {prev:.2f}→{topic_weights[t]:.2f}"
            )
    elif best_score > 30:
        for t in topics:
            prev = topic_weights.get(t, 0)
            topic_weights[t] = round(prev + 0.1, 3)
            evolution_log.append(
                f"「{t}」重みを強化（高スコア）: {prev:.2f}→{topic_weights[t]:.2f}"
            )

    memory["topic_weights"] = topic_weights
    memory["evolution_log"].extend(evolution_log)
    memory["evolution_log"] = memory["evolution_log"][-10:]

    return memory, evolution_log

# ============================================================
# メインループ ver4.1
# ============================================================

def rethink_ai(query, memory, conversation_history=None):
    purpose = detect_purpose(query)
    topics = extract_topics(query)
    candidates = generate_answers(query, purpose, conversation_history)

    if not candidates:
        return None, memory, None

    graph = memory["graph"]
    last_updated = memory["last_updated"]
    topic_weights = memory.get("topic_weights", {})

    bonus = graph_bonus_score(graph, topics)
    t_bonus = topic_bonus_score(topic_weights, topics)

    removed = set()
    best = None
    best_score = -999
    contradiction = None
    contradiction_question = None

    for round_num in range(1, 4):
        active = [c for c in candidates if c["id"] not in removed]
        if not active:
            break

        scored = sorted(
            active,
            key=lambda c: score(c, purpose["weight"], bonus, t_bonus),
            reverse=True
        )

        top_score = score(scored[0], purpose["weight"], bonus, t_bonus)

        if top_score > best_score:
            best_score = top_score
            best = scored[0]

        contradiction = detect_contradiction(scored, purpose["weight"], bonus)
        if contradiction:
            contradiction_question = generate_questions_from_contradiction(
                contradiction, topics, query
            )

        graph, last_updated = update_graph(
            graph, last_updated, topics, top_score / 100
        )

        if round_num >= 3:
            break

        removed.add(scored[-1]["id"])

    memory["graph"] = graph
    memory["last_updated"] = last_updated

    # 自己検証
    verification = self_verify(query, best["text"], topics)

    # 自己進化
    memory, evolution_log = evolve(memory, topics, best_score, contradiction)

    save_memory(memory)

    spark_pick, sparked = probabilistic_spark(
        sorted(candidates,
               key=lambda c: score(c, purpose["weight"], bonus, t_bonus),
               reverse=True)
    )

    patterns = graph_pattern(graph, topics)

    return best, memory, {
        "purpose":    purpose,
        "topics":     topics,
        "bonus":      bonus,
        "t_bonus":    t_bonus,
        "best":       best,
        "best_score": best_score,
        "candidates": sorted(
            candidates,
            key=lambda c: score(c, purpose["weight"], bonus, t_bonus),
            reverse=True
        ),
        "sparked":       sparked,
        "spark_pick":    spark_pick,
        "patterns":      patterns,
        "contradiction": contradiction,
        "contradiction_question": contradiction_question,
        "evolution_log": evolution_log,
        "verification":  verification,
    }

# ============================================================
# Streamlit画面
# ============================================================

st.set_page_config(
    page_title="再思考AI",
    page_icon="🧠",
    layout="centered"
)

st.title("🧠 再思考AI ver4.1")
st.caption("自己検証・会話継続・自己進化する再思考AI")

# 記憶を読み込む
if "memory" not in st.session_state:
    memory = load_memory()
    memory["graph"] = decay_memory(
        memory["graph"],
        memory["last_updated"]
    )
    st.session_state.memory = memory

# 会話履歴
if "conversation_history" not in st.session_state:
    st.session_state.conversation_history = []

# 会話履歴の表示
if st.session_state.conversation_history:
    st.subheader("💬 会話の流れ")
    for i, h in enumerate(st.session_state.conversation_history):
        with st.expander(f"問い{i+1}：{h['query'][:30]}..."):
            st.write(h['answer'])

# 入力欄
query = st.text_area(
    "質問を入力してください",
    placeholder="例：料理が上手くなりたい",
    height=100
)

col1, col2 = st.columns([1, 1])
with col1:
    think_btn = st.button("🔍 考える", type="primary")
with col2:
    clear_btn = st.button("🗑️ 会話をリセット")

if clear_btn:
    st.session_state.conversation_history = []
    st.rerun()

if think_btn:
    if not query.strip():
        st.warning("質問を入力してください")
    else:
        with st.spinner("考え中..."):
            best, memory, result = rethink_ai(
                query,
                st.session_state.memory,
                st.session_state.conversation_history
            )
            st.session_state.memory = memory

        if result is None:
            st.error("生成に失敗しました。もう一度試してください。")
        else:
            purpose_jp = {
                "short": "⚡ すぐ使える",
                "long":  "📚 本質重視",
                "mid":   "⚖️ バランス",
            }[result["purpose"]["type"]]

            bonus_text = ""
            if result["bonus"] > 0:
                bonus_text += f" ／ 経験+{result['bonus']:.2f}"
            if result["t_bonus"] > 0:
                bonus_text += f" ／ 進化+{result['t_bonus']:.2f}"

            st.info(
                f"**目的：** {purpose_jp} ／ "
                f"**トピック：** {', '.join(result['topics'])}"
                + bonus_text
            )

            # 矛盾探知
            if result["contradiction"] and result["contradiction_question"]:
                st.warning(
                    f"⚠️ **矛盾を検出！**\n\n"
                    f"🤔 **自動生成した問い：**\n"
                    f"「{result['contradiction_question']['question']}」\n\n"
                    f"💡 理由：{result['contradiction_question']['reason']}"
                )

            st.subheader("📋 候補一覧")
            labels = ["🥇 メイン視点", "🥈 サブ視点", "🥉 第三視点"]

            for i, c in enumerate(result["candidates"][:3]):
                s = score(
                    c,
                    result["purpose"]["weight"],
                    result["bonus"],
                    result["t_bonus"]
                )
                is_spark = (
                    result["sparked"] and
                    c["id"] == result["spark_pick"]["id"]
                )
                spark = " ⚡閃き" if is_spark else ""
                st.markdown(
                    f"**{labels[i]}{spark}** （score: {s:.1f}）\n\n"
                    f"{c['text']}"
                )
                st.divider()

            st.success(
                f"✅ **推奨** （score: {result['best_score']:.1f}）\n\n"
                f"**{result['best']['text']}**"
            )

            # 自己検証の表示
            verification = result["verification"]
            if not verification["sufficient"] and verification["follow_up"]:
                st.info(
                    f"🤖 **自己検証：この答えで十分か確認しました**\n\n"
                    f"判定：{'✅ 十分' if verification['sufficient'] else '❓ 追加情報が必要'}\n"
                    f"理由：{verification['reason']}\n\n"
                    f"💬 **続けて答えてください：**\n"
                    f"「{verification['follow_up']}」"
                )
            else:
                st.info(
                    f"🤖 **自己検証：** {verification['reason']}"
                )

            if result["patterns"]:
                st.info(
                    f"💡 **パターン検出：** "
                    f"「{result['patterns'][0][0]}」との"
                    f"つながりを発見！"
                )

            if result["evolution_log"]:
                with st.expander("🔬 自己進化ログ"):
                    for log in result["evolution_log"]:
                        st.text(log)

            # 会話履歴に追加
            st.session_state.conversation_history.append({
                "query":  query,
                "answer": result["best"]["text"],
                "topics": list(result["topics"]),
            })
