import streamlit as st
import random
import time
import json
import os
from datetime import datetime
from google import genai

# ============================================================
# 再思考AI - Streamlit版
# ============================================================

client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

MEMORY_FILE = "memory.json"

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"graph": {}, "last_updated": {}}

def save_memory(graph, last_updated):
    data = {
        "graph": graph,
        "last_updated": last_updated,
        "saved_at": datetime.now().isoformat()
    }
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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

def detect_purpose(text):
    short = sum(1 for w in ["すぐ","今","早く","簡単"] if w in text)
    long  = sum(1 for w in ["本質","しっかり","根本","ちゃんと"] if w in text)
    if short > long:   return {"type": "short", "weight": 0.3}
    elif long > short: return {"type": "long",  "weight": 0.8}
    else:              return {"type": "mid",   "weight": 0.5}

def score(answer, purpose_weight, graph_bonus=0):
    return round(
        answer["confidence"] * 60 * purpose_weight
        - answer["variation"] * 0.25
        - answer["length"] * 0.1
        + graph_bonus
    , 2)

def graph_bonus_score(graph, topics):
    total = 0
    for t in topics:
        if t in graph:
            total += sum(graph[t].values())
    return round(total * 2, 2)

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

def calc_density(scored, purpose_weight, graph_bonus):
    if len(scored) < 2:
        return 0.0
    scores = [score(c, purpose_weight, graph_bonus) for c in scored]
    top    = scores[0]
    diffs  = [abs(top - s) for s in scores[1:]]
    avg_diff = sum(diffs) / len(diffs)
    return max(0, round(100 - avg_diff * 5, 1))

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

def generate_answers(query, purpose):
    style = {
        "short": "すぐ実践できる具体的な方法",
        "long":  "本質的な理解につながる方法",
        "mid":   "バランスのとれた方法",
    }[purpose["type"]]

    prompt = f"""
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

def rethink_ai(query, graph, last_updated):
    purpose = detect_purpose(query)
    topics = extract_topics(query)
    candidates = generate_answers(query, purpose)

    if not candidates:
        return None, graph, last_updated, None

    bonus = graph_bonus_score(graph, topics)
    removed = set()
    best = None
    best_score = -999

    for round_num in range(1, 4):
        active = [c for c in candidates if c["id"] not in removed]
        if not active:
            break

        scored = sorted(
            active,
            key=lambda c: score(c, purpose["weight"], bonus),
            reverse=True
        )

        top_score = score(scored[0], purpose["weight"], bonus)

        if top_score > best_score:
            best_score = top_score
            best = scored[0]

        graph, last_updated = update_graph(
            graph, last_updated, topics, top_score / 100
        )

        if round_num >= 3:
            break

        removed.add(scored[-1]["id"])

    save_memory(graph, last_updated)

    spark_pick, sparked = probabilistic_spark(
        sorted(candidates,
               key=lambda c: score(c, purpose["weight"], bonus),
               reverse=True)
    )

    patterns = graph_pattern(graph, topics)

    return best, graph, last_updated, {
        "purpose":    purpose,
        "topics":     topics,
        "bonus":      bonus,
        "best":       best,
        "best_score": best_score,
        "candidates": sorted(
            candidates,
            key=lambda c: score(c, purpose["weight"], bonus),
            reverse=True
        ),
        "sparked":    sparked,
        "spark_pick": spark_pick,
        "patterns":   patterns,
    }

# ============================================================
# Streamlit画面
# ============================================================

st.set_page_config(
    page_title="再思考AI",
    page_icon="🧠",
    layout="centered"
)

st.title("🧠 再思考AI")
st.caption("どんな問いでも3つの視点から考えます")

# 記憶を読み込む
if "graph" not in st.session_state:
    memory = load_memory()
    st.session_state.graph = memory["graph"]
    st.session_state.last_updated = memory["last_updated"]
    st.session_state.graph = decay_memory(
        st.session_state.graph,
        st.session_state.last_updated
    )

# 入力欄
query = st.text_area(
    "質問を入力してください",
    placeholder="例：料理が上手くなりたい",
    height=100
)

if st.button("🔍 考える", type="primary"):
    if not query.strip():
        st.warning("質問を入力してください")
    else:
        with st.spinner("考え中..."):
            best, graph, last_updated, result = rethink_ai(
                query,
                st.session_state.graph,
                st.session_state.last_updated
            )
            st.session_state.graph = graph
            st.session_state.last_updated = last_updated

        if result is None:
            st.error("生成に失敗しました。もう一度試してください。")
        else:
            purpose_jp = {
                "short": "⚡ すぐ使える",
                "long":  "📚 本質重視",
                "mid":   "⚖️ バランス",
            }[result["purpose"]["type"]]

            st.info(
                f"**目的：** {purpose_jp} ／ "
                f"**トピック：** {', '.join(result['topics'])}"
                + (f" ／ **経験ボーナス：** +{result['bonus']:.2f}"
                   if result['bonus'] > 0 else "")
            )

            st.subheader("📋 候補一覧")
            labels = ["🥇 メイン視点", "🥈 サブ視点", "🥉 第三視点"]
            colors = ["#fff9e6", "#f0fff0", "#f0f8ff"]

            for i, c in enumerate(result["candidates"][:3]):
                s = score(c, result["purpose"]["weight"], result["bonus"])
                is_spark = (
                    result["sparked"] and
                    c["id"] == result["spark_pick"]["id"]
                )
                spark = " ⚡閃き" if is_spark else ""
                st.markdown(
                    f"**{labels[i]}{spark}** （score: {s:.1f}）\n\n"
                    f"{c['text']}",
                )
                st.divider()

            st.success(
                f"✅ **推奨** （score: {result['best_score']:.1f}）\n\n"
                f"**{result['best']['text']}**"
            )

            if result["patterns"]:
                st.warning(
                    f"💡 **パターン検出：** "
                    f"「{result['patterns'][0][0]}」との"
                    f"つながりを発見！"
                )
