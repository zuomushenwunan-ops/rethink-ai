import streamlit as st
import random
import time
import json
import os
import math
from datetime import datetime
from google import genai

# ============================================================
# 再思考AI ver4.3 - 情報トポロジー＋三角錐収束
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
        "topology": {},  # 情報トポロジー
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

def calc_info_distance(strength):
    """強度から距離を計算（強いほど近い）"""
    if strength <= 0:
        return 1.0
    return round(1 - strength, 3)

def update_topology(topology, topics, scores):
    """情報トポロジーを更新"""
    topics = list(topics)
    for i in range(len(topics)):
        for j in range(i+1, len(topics)):
            a, b = topics[i], topics[j]
            key = f"{a}↔{b}"
            avg_score = sum(scores) / len(scores) if scores else 0
            strength = avg_score / 100

            if key not in topology:
                topology[key] = {
                    "強度": 0,
                    "距離": 1.0,
                    "連結性": "弱",
                }

            topology[key]["強度"] = round(
                topology[key]["強度"] * 0.7 + strength * 0.3, 3
            )
            topology[key]["距離"] = calc_info_distance(
                topology[key]["強度"]
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

def build_pyramid(candidates, topics, purpose_weight, bonus, t_bonus):
    """
    3つの視点を三角錐として収束させる
    底面：3つの視点（候補）
    頂点：収束した最終答え
    """
    if len(candidates) < 3:
        return None

    scored = sorted(
        candidates,
        key=lambda c: score(c, purpose_weight, bonus, t_bonus),
        reverse=True
    )[:3]

    # 底面の各辺の距離を計算
    s = [score(c, purpose_weight, bonus, t_bonus) for c in scored]

    ab_dist = round(abs(s[0] - s[1]) / 100, 3)
    bc_dist = round(abs(s[1] - s[2]) / 100, 3)
    ca_dist = round(abs(s[2] - s[0]) / 100, 3)

    # 収束スコア（距離が小さいほど収束してる）
    avg_dist = (ab_dist + bc_dist + ca_dist) / 3
    convergence = round(1 - avg_dist, 3)

    # 頂点（収束した答え）を生成
    # スコアの重み付き平均で最終答えを決定
    total_score = sum(max(s_i, 0.001) for s_i in s)
    weights = [max(s_i, 0.001) / total_score for s_i in s]

    return {
        "底面": [
            {
                "視点": ["メイン", "サブ", "第三"][i],
                "答え": scored[i]["text"],
                "score": s[i],
                "重み": round(weights[i], 3),
            }
            for i in range(3)
        ],
        "辺": {
            "メイン↔サブ距離": ab_dist,
            "サブ↔第三距離": bc_dist,
            "第三↔メイン距離": ca_dist,
        },
        "収束スコア": convergence,
        "頂点": scored[0]["text"],  # 最高スコアが頂点
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
    complexity_signals = [
        "すべて","全て","全部","プロセス","手順","ステップ",
        "詳しく","詳細","具体的に","計算","証明","説明して",
        "条件","制約","厳守","連鎖","逐次","順番に",
    ]
    score_c = sum(1 for w in complexity_signals if w in query)
    length_score = len(query) / 100
    total = score_c + length_score
    if total >= 3:    return "complex"
    elif total >= 1.5: return "medium"
    else:             return "simple"

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

def generate_answers(query, purpose, complexity, conversation_history=None):
    style = {
        "short": "すぐ実践できる具体的な方法",
        "long":  "本質的な理解につながる方法",
        "mid":   "バランスのとれた方法",
    }[purpose["type"]]

    length_instruction = {
        "complex": "文字数制限なし・全プロセスを詳細に記述",
        "medium":  "300文字程度で詳しく",
        "simple":  "100文字以内で簡潔に",
    }[complexity]

    history_text = ""
    if conversation_history:
        history_text = "\n\n【これまでの会話】\n"
        for h in conversation_history[-3:]:
            history_text += f"問い：{h['query']}\n"
            history_text += f"答え：{h['answer']}\n\n"

    prompt = f"""
{history_text}
「{query}」に対して、{style}で3つの異なる視点から答えてください。

文字数：{length_instruction}

必ずこのJSON形式のみで返してください（他の文字は不要）:
[
  {{"text": "答え1", "confidence": 0.9}},
  {{"text": "答え2", "confidence": 0.7}},
  {{"text": "答え3", "confidence": 0.5}}
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

def self_verify(query, best_answer, topics):
    prompt = f"""
問い：「{query}」
答え：「{best_answer}」
トピック：{', '.join(topics)}

この答えを自己検証してください。

必ずこのJSON形式のみで返してください：
{{
  "sufficient": true or false,
  "reason": "理由（30文字以内）",
  "follow_up": "追加質問（不十分でない場合はnull）"
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
        return {"sufficient": True, "reason": "検証完了", "follow_up": None}

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
元の問い：「{query}」
答えA：「{contradiction['候補A']}」
答えB：「{contradiction['候補B']}」
関連トピック：{topics_str}

必ずこのJSON形式のみで返してください：
{{"question": "問いの内容", "reason": "理由（30文字以内）"}}
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
                f"「{t}」重みを調整: {prev:.2f}→{topic_weights[t]:.2f}"
            )
    elif best_score > 30:
        for t in topics:
            prev = topic_weights.get(t, 0)
            topic_weights[t] = round(prev + 0.1, 3)
            evolution_log.append(
                f"「{t}」重みを強化: {prev:.2f}→{topic_weights[t]:.2f}"
            )

    memory["topic_weights"] = topic_weights
    memory["evolution_log"].extend(evolution_log)
    memory["evolution_log"] = memory["evolution_log"][-10:]

    return memory, evolution_log

# ============================================================
# メインループ ver4.3
# ============================================================

def rethink_ai(query, memory, conversation_history=None):
    purpose    = detect_purpose(query)
    complexity = detect_complexity(query)
    topics     = extract_topics(query)
    candidates = generate_answers(
        query, purpose, complexity, conversation_history
    )

    if not candidates:
        return None, memory, None

    graph         = memory["graph"]
    last_updated  = memory["last_updated"]
    topic_weights = memory.get("topic_weights", {})
    topology      = memory.get("topology", {})

    bonus   = graph_bonus_score(graph, topics)
    t_bonus = topic_bonus_score(topic_weights, topics)

    removed               = set()
    best                  = None
    best_score_val        = -999
    contradiction         = None
    contradiction_question = None
    all_scores            = []

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
        all_scores.append(top_score)

        if top_score > best_score_val:
            best_score_val = top_score
            best = scored[0]

        contradiction = detect_contradiction(
            scored, purpose["weight"], bonus
        )
        if contradiction:
            contradiction_question = (
                generate_questions_from_contradiction(
                    contradiction, topics, query
                )
            )

        graph, last_updated = update_graph(
            graph, last_updated, topics, top_score / 100
        )

        if round_num >= 3:
            break

        removed.add(scored[-1]["id"])

    memory["graph"]        = graph
    memory["last_updated"] = last_updated

    # 情報トポロジー更新
    topology = update_topology(topology, topics, all_scores)
    memory["topology"] = topology

    # 三角錐収束
    pyramid = build_pyramid(
        candidates, topics, purpose["weight"], bonus, t_bonus
    )

    verification = self_verify(query, best["text"], topics)
    memory, evolution_log = evolve(
        memory, topics, best_score_val, contradiction
    )

    save_memory(memory)

    spark_pick, sparked = probabilistic_spark(
        sorted(candidates,
               key=lambda c: score(
                   c, purpose["weight"], bonus, t_bonus
               ),
               reverse=True)
    )

    patterns = graph_pattern(graph, topics)

    return best, memory, {
        "purpose":    purpose,
        "complexity": complexity,
        "topics":     topics,
        "bonus":      bonus,
        "t_bonus":    t_bonus,
        "best":       best,
        "best_score": best_score_val,
        "candidates": sorted(
            candidates,
            key=lambda c: score(
                c, purpose["weight"], bonus, t_bonus
            ),
            reverse=True
        ),
        "sparked":                sparked,
        "spark_pick":             spark_pick,
        "patterns":               patterns,
        "contradiction":          contradiction,
        "contradiction_question": contradiction_question,
        "evolution_log":          evolution_log,
        "verification":           verification,
        "pyramid":                pyramid,
        "topology":               topology,
    }

# ============================================================
# Streamlit画面
# ============================================================

st.set_page_config(
    page_title="再思考AI",
    page_icon="🧠",
    layout="centered"
)

st.title("🧠 再思考AI ver4.3")
st.caption("情報トポロジー＋三角錐収束・自己進化する再思考AI")

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
        complexity = detect_complexity(query)
        complexity_jp = {
            "complex": "🔴 複雑（詳細モード）",
            "medium":  "🟡 中程度（標準モード）",
            "simple":  "🟢 シンプル（簡潔モード）",
        }[complexity]

        with st.spinner(f"考え中... {complexity_jp}"):
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

            # 三角錐収束の表示
            if result["pyramid"]:
                p = result["pyramid"]
                convergence = p["収束スコア"]
                conv_label = (
                    "🔺 高収束（視点が一致）" if convergence > 0.7 else
                    "🔸 中収束" if convergence > 0.4 else
                    "🔹 低収束（視点が分散）"
                )
                with st.expander(
                    f"🔺 三角錐収束 （収束スコア: {convergence}）"
                    f" {conv_label}"
                ):
                    st.markdown("**底面（3つの視点）：**")
                    for face in p["底面"]:
                        st.markdown(
                            f"- **{face['視点']}** "
                            f"（重み: {face['重み']}）: "
                            f"{face['答え'][:50]}..."
                        )
                    st.markdown("**辺（視点間の距離）：**")
                    for edge, dist in p["辺"].items():
                        closeness = "近い" if dist < 0.3 else "遠い"
                        st.markdown(f"- {edge}: {dist} ({closeness})")
                    st.markdown(
                        f"**頂点（収束した答え）：**\n\n"
                        f"{p['頂点']}"
                    )

            # 情報トポロジーの表示
            relevant_topology = {
                k: v for k, v in result["topology"].items()
                if any(t in k for t in result["topics"])
            }
            if relevant_topology:
                with st.expander("🌐 情報トポロジー"):
                    for key, val in relevant_topology.items():
                        st.markdown(
                            f"**{key}**：強度={val['強度']} ／ "
                            f"距離={val['距離']} ／ "
                            f"連結性={val['連結性']}"
                        )

            verification = result["verification"]
            if not verification["sufficient"] and verification["follow_up"]:
                st.info(
                    f"🤖 **自己検証：追加情報が必要**\n\n"
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

            st.session_state.conversation_history.append({
                "query":  query,
                "answer": result["best"]["text"],
                "topics": list(result["topics"]),
            })
