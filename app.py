import streamlit as st
import random
import time
import json
import os
import math
from datetime import datetime
from google import genai

# ============================================================
# 再思考AI ver5.0 - 本物の再思考エンジン
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
        "topology": {},
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
# 情報トポロジー
# ============================================================

def update_topology(topology, topics, scores):
    topics = list(topics)
    for i in range(len(topics)):
        for j in range(i+1, len(topics)):
            a, b = topics[i], topics[j]
            key = f"{a}↔{b}"
            avg_score = sum(scores) / len(scores) if scores else 0
            strength = avg_score / 100
            if key not in topology:
                topology[key] = {"強度": 0, "距離": 1.0, "連結性": "弱"}
            topology[key]["強度"] = round(
                topology[key]["強度"] * 0.7 + strength * 0.3, 3
            )
            topology[key]["距離"] = round(
                1 - topology[key]["強度"], 3
            )
            topology[key]["連結性"] = (
                "強" if topology[key]["強度"] > 0.6 else
                "中" if topology[key]["強度"] > 0.3 else
                "弱"
            )
    return topology

# ============================================================
# 三角錐収束
# ============================================================

def build_pyramid(steps, purpose_weight, bonus, t_bonus):
    if len(steps) < 3:
        return None

    scores = [s["score"] for s in steps[-3:]]
    last3  = steps[-3:]

    ab = round(abs(scores[0] - scores[1]) / 100, 3)
    bc = round(abs(scores[1] - scores[2]) / 100, 3)
    ca = round(abs(scores[2] - scores[0]) / 100, 3)
    convergence = round(1 - (ab + bc + ca) / 3, 3)

    return {
        "底面": [
            {
                "ステップ": i+1,
                "答え": last3[i]["answer"][:60] + "...",
                "score": scores[i],
                "弱点": last3[i].get("weakness", ""),
            }
            for i in range(3)
        ],
        "辺": {
            "Step1↔Step2距離": ab,
            "Step2↔Step3距離": bc,
            "Step3↔Step1距離": ca,
        },
        "収束スコア": convergence,
        "頂点": last3[-1]["answer"],
    }

# ============================================================
# コア関数
# ============================================================

def detect_purpose(text):
    short = sum(1 for w in ["すぐ","今","早く","簡単"] if w in text)
    long  = sum(1 for w in ["本質","しっかり","根本","ちゃんと"] if w in text)
    if short > long:   return {"type": "short", "weight": 0.3}
    elif long > short: return {"type": "long",  "weight": 0.8}
    else:              return {"type": "mid",   "weight": 0.5}

def detect_complexity(query):
    signals = [
        "すべて","全て","プロセス","手順","詳しく","詳細",
        "計算","証明","条件","制約","連鎖","逐次",
    ]
    sc = sum(1 for w in signals if w in query)
    total = sc + len(query) / 100
    if total >= 3:     return "complex"
    elif total >= 1.5: return "medium"
    else:              return "simple"

def score_answer(answer, purpose_weight, graph_bonus=0, topic_bonus=0):
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
    return round(sum(topic_weights.get(t, 0) for t in topics), 2)

def graph_pattern(graph, topics):
    hits = []
    for t in topics:
        if t in graph:
            for connected, strength in graph[t].items():
                if connected not in topics and strength > 0.3:
                    hits.append((connected, strength))
    return sorted(hits, key=lambda x: x[1], reverse=True)

def extract_topics(query):
    prompt = f"""
以下の文章から具体的なトピックを3つ抽出してください。

文章:「{query}」

ルール：
- 各トピックは2〜6文字の日本語
- 「一般」「理解」など曖昧な言葉は使わない
- 必ずこのJSON形式のみで返す

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
                if t not in ["一般","理解","学習","練習","上達"]
            ]
            return set(filtered) if len(filtered) >= 2 else set(topics[:3])
        except:
            if attempt < 2:
                time.sleep(3)
    return {"スキル習得", "反復練習"}

# ============================================================
# 本物の再思考エンジン
# ============================================================

def first_answer(query, purpose, complexity):
    """ステップ1：最初の答えを出す"""
    length = {
        "complex": "詳細に",
        "medium":  "300文字程度で",
        "simple":  "100文字以内で",
    }[complexity]

    prompt = f"""
「{query}」に対して、{length}現実的・科学的な根拠に基づいて答えてください。

ルール：
- 投機的・SF的な答えは避ける
- 現在の研究で議論されている範囲内で
- 必ずこのJSON形式のみで返す

{{"answer": "答え", "confidence": 0.0〜1.0}}
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        text = response.text.strip()
        start = text.find("{")
        end   = text.rfind("}") + 1
        data  = json.loads(text[start:end])
        return {
            "answer":     data["answer"],
            "confidence": float(data["confidence"]),
            "variation":  20,
            "length":     round(len(data["answer"]) / 10, 1),
            "step":       1,
        }
    except:
        return None

def find_weakness(query, answer, step):
    """ステップN：前の答えの弱点を見つける"""
    prompt = f"""
問い：「{query}」
前の答え（ステップ{step}）：「{answer}」

この答えの最も重要な弱点・不足点を1つ指摘してください。

ルール：
- 現実的・論理的な観点から
- 感情的・SF的な指摘は避ける
- 必ずこのJSON形式のみで返す

{{"weakness": "弱点（50文字以内）", "direction": "改善の方向性（30文字以内）"}}
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
        return {"weakness": "不明", "direction": "別の視点から考える"}

def rethink_answer(query, prev_answer, weakness, direction,
                   purpose, complexity, step):
    """ステップN+1：弱点を踏まえて考え直す"""
    length = {
        "complex": "詳細に",
        "medium":  "300文字程度で",
        "simple":  "100文字以内で",
    }[complexity]

    prompt = f"""
問い：「{query}」

前の答え：「{prev_answer}」
前の答えの弱点：「{weakness}」
改善の方向性：「{direction}」

この弱点を克服した、より深い答えを{length}考えてください。

ルール：
- 現実的・科学的な根拠に基づく
- 投機的・SF的な答えは避ける
- 前の答えより具体的・深く
- 必ずこのJSON形式のみで返す

{{"answer": "答え", "confidence": 0.0〜1.0, "improvement": "前の答えからの改善点（30文字以内）"}}
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        text = response.text.strip()
        start = text.find("{")
        end   = text.rfind("}") + 1
        data  = json.loads(text[start:end])
        return {
            "answer":      data["answer"],
            "confidence":  float(data["confidence"]),
            "improvement": data.get("improvement", ""),
            "variation":   15,
            "length":      round(len(data["answer"]) / 10, 1),
            "step":        step,
        }
    except:
        return None

def evolve(memory, topics, best_score, has_contradiction):
    evolution_log = []
    topic_weights = memory.get("topic_weights", {})

    if has_contradiction:
        for t in topics:
            prev = topic_weights.get(t, 0)
            topic_weights[t] = round(prev - 0.05, 3)
            evolution_log.append(
                f"「{t}」調整: {prev:.2f}→{topic_weights[t]:.2f}"
            )
    elif best_score > 30:
        for t in topics:
            prev = topic_weights.get(t, 0)
            topic_weights[t] = round(prev + 0.1, 3)
            evolution_log.append(
                f"「{t}」強化: {prev:.2f}→{topic_weights[t]:.2f}"
            )

    memory["topic_weights"] = topic_weights
    memory["evolution_log"].extend(evolution_log)
    memory["evolution_log"] = memory["evolution_log"][-10:]
    return memory, evolution_log

# ============================================================
# メインループ ver5.0
# ============================================================

def rethink_ai(query, memory, conversation_history=None):
    purpose    = detect_purpose(query)
    complexity = detect_complexity(query)
    topics     = extract_topics(query)

    graph         = memory["graph"]
    last_updated  = memory["last_updated"]
    topic_weights = memory.get("topic_weights", {})
    topology      = memory.get("topology", {})

    bonus   = graph_bonus_score(graph, topics)
    t_bonus = topic_bonus_score(topic_weights, topics)

    # ============================================================
    # 本物の再思考ループ
    # ステップ1：最初の答え
    # ステップ2：弱点発見→考え直す
    # ステップ3：さらに弱点発見→考え直す
    # ============================================================

    steps = []
    all_scores = []

    # ステップ1
    ans1 = first_answer(query, purpose, complexity)
    if not ans1:
        return None, memory, None

    s1 = score_answer(ans1, purpose["weight"], bonus, t_bonus)
    steps.append({
        "step":     1,
        "answer":   ans1["answer"],
        "score":    s1,
        "weakness": "",
        "improvement": "最初の答え",
    })
    all_scores.append(s1)

    graph, last_updated = update_graph(
        graph, last_updated, topics, s1 / 100
    )

    # ステップ2：弱点を見つけて考え直す
    w1 = find_weakness(query, ans1["answer"], 1)
    ans2 = rethink_answer(
        query, ans1["answer"],
        w1["weakness"], w1["direction"],
        purpose, complexity, 2
    )

    if ans2:
        s2 = score_answer(ans2, purpose["weight"], bonus, t_bonus)
        steps.append({
            "step":        2,
            "answer":      ans2["answer"],
            "score":       s2,
            "weakness":    w1["weakness"],
            "improvement": ans2.get("improvement", ""),
        })
        all_scores.append(s2)
        graph, last_updated = update_graph(
            graph, last_updated, topics, s2 / 100
        )

        # ステップ3：さらに弱点を見つけて考え直す
        w2 = find_weakness(query, ans2["answer"], 2)
        ans3 = rethink_answer(
            query, ans2["answer"],
            w2["weakness"], w2["direction"],
            purpose, complexity, 3
        )

        if ans3:
            s3 = score_answer(ans3, purpose["weight"], bonus, t_bonus)
            steps.append({
                "step":        3,
                "answer":      ans3["answer"],
                "score":       s3,
                "weakness":    w2["weakness"],
                "improvement": ans3.get("improvement", ""),
            })
            all_scores.append(s3)
            graph, last_updated = update_graph(
                graph, last_updated, topics, s3 / 100
            )

    memory["graph"]        = graph
    memory["last_updated"] = last_updated

    # 情報トポロジー更新
    topology = update_topology(topology, topics, all_scores)
    memory["topology"] = topology

    # 最良ステップを選ぶ
    best_step  = max(steps, key=lambda s: s["score"])
    best_score = best_step["score"]

    # 三角錐収束
    pyramid = build_pyramid(steps, purpose["weight"], bonus, t_bonus)

    # 自己進化
    memory, evolution_log = evolve(
        memory, topics, best_score, False
    )

    save_memory(memory)

    patterns = graph_pattern(graph, topics)

    return best_step, memory, {
        "purpose":       purpose,
        "complexity":    complexity,
        "topics":        topics,
        "bonus":         bonus,
        "t_bonus":       t_bonus,
        "best":          best_step,
        "best_score":    best_score,
        "steps":         steps,
        "patterns":      patterns,
        "evolution_log": evolution_log,
        "pyramid":       pyramid,
        "topology":      topology,
    }

# ============================================================
# Streamlit画面
# ============================================================

st.set_page_config(
    page_title="再思考AI",
    page_icon="🧠",
    layout="centered"
)

st.title("🧠 再思考AI ver5.0")
st.caption("本物の再思考エンジン・深化するたびに賢くなる")

if "memory" not in st.session_state:
    memory = load_memory()
    memory["graph"] = decay_memory(
        memory["graph"],
        memory["last_updated"]
    )
    st.session_state.memory = memory

if "conversation_history" not in st.session_state:
    st.session_state.conversation_history = []

if st.session_state.conversation_history:
    st.subheader("💬 会話の流れ")
    for i, h in enumerate(st.session_state.conversation_history):
        with st.expander(f"問い{i+1}：{h['query'][:30]}..."):
            st.write(h['answer'])

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
        complexity    = detect_complexity(query)
        complexity_jp = {
            "complex": "🔴 複雑（詳細モード）",
            "medium":  "🟡 中程度（標準モード）",
            "simple":  "🟢 シンプル（簡潔モード）",
        }[complexity]

        with st.spinner(f"再思考中... {complexity_jp}"):
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
                f"**複雑さ：** {complexity_jp} ／ "
                f"**トピック：** {', '.join(result['topics'])}"
                + bonus_text
            )

            # 再思考プロセスを表示
            st.subheader("🔄 再思考プロセス")
            for step in result["steps"]:
                if step["step"] == 1:
                    label = "💭 ステップ1：最初の答え"
                else:
                    label = (
                        f"🔄 ステップ{step['step']}："
                        f"再思考（弱点：{step['weakness']}）"
                    )

                with st.expander(
                    f"{label} （score: {step['score']:.1f}）"
                ):
                    st.write(step["answer"])
                    if step.get("improvement") and step["step"] > 1:
                        st.caption(
                            f"改善点：{step['improvement']}"
                        )

            # 最終答え
            st.success(
                f"✅ **最終答え** "
                f"（ステップ{result['best']['step']} / "
                f"score: {result['best_score']:.1f}）\n\n"
                f"**{result['best']['answer']}**"
            )

            # 三角錐収束
            if result["pyramid"]:
                p = result["pyramid"]
                convergence = p["収束スコア"]
                conv_label = (
                    "🔺 高収束" if convergence > 0.7 else
                    "🔸 中収束" if convergence > 0.4 else
                    "🔹 低収束（思考が発散・深化中）"
                )
                with st.expander(
                    f"🔺 三角錐収束 "
                    f"（収束スコア: {convergence}）{conv_label}"
                ):
                    for face in p["底面"]:
                        st.markdown(
                            f"- **ステップ{face['ステップ']}** "
                            f"（score: {face['score']:.1f}）: "
                            f"{face['答え']}"
                        )
                    st.markdown("**視点間の距離：**")
                    for edge, dist in p["辺"].items():
                        closeness = "近い" if dist < 0.3 else "遠い"
                        st.markdown(
                            f"- {edge}: {dist} ({closeness})"
                        )

            # 情報トポロジー
            relevant = {
                k: v for k, v in result["topology"].items()
                if any(t in k for t in result["topics"])
            }
            if relevant:
                with st.expander("🌐 情報トポロジー"):
                    for key, val in relevant.items():
                        st.markdown(
                            f"**{key}**：強度={val['強度']} ／ "
                            f"距離={val['距離']} ／ "
                            f"連結性={val['連結性']}"
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

            st.session_state.conversation_history.append({
                "query":  query,
                "answer": result["best"]["answer"],
                "topics": list(result["topics"]),
            })
