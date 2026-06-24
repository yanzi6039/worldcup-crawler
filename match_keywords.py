"""
比赛关键词生成器
对每场比赛生成多层关键词，用于 RSS 过滤和文章打标

返回结构：
{
  "tier_A": ["Spain vs Saudi Arabia", "西班牙 vs 沙特"],  # 直接相关
  "tier_B": ["Spain World Cup", "Saudi Arabia preview"],  # 单方+预览
  "tier_C": ["Yamal", "Morata"],                          # 球员
  "tier_D": ["Group H", "Group of death"],                # 小组
  "tier_E": ["Spain Saudi Arabia history"],               # H2H
}
"""
import os
import sys
import json
import re
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from db import store

log = logging.getLogger("match_keywords")

# 缓存国家关键词
_COUNTRY_KW_CACHE = None
_PLAYER_KW_CACHE = None
_COUNTRY_NAME_CACHE = None


def _load_country_keywords() -> dict:
    """国家 ID → 关键词列表（含中英文名 + 别名）"""
    global _COUNTRY_KW_CACHE
    if _COUNTRY_KW_CACHE is None:
        cmap = {}
        for c in store.list_countries():
            kws = set()
            for n in (c.get("name_cn"), c.get("name_en")):
                if n: kws.add(n)
            try:
                extra = json.loads(c.get("keywords") or "[]")
                for k in extra:
                    if k and len(k) >= 2:
                        kws.add(k)
            except Exception:
                pass
            cmap[c["id"]] = list(kws)
        _COUNTRY_KW_CACHE = cmap
    return _COUNTRY_KW_CACHE


def _load_player_keywords(country_id: int = None) -> list:
    """某国的球员关键词"""
    players = store.list_players(country_id=country_id) if country_id else store.list_players()
    kws = []
    for p in players:
        names = []
        for n in (p.get("name_cn"), p.get("name_en")):
            if n: names.append(n)
        try:
            extra = json.loads(p.get("keywords") or "[]")
            names.extend(extra)
        except Exception:
            pass
        # 只保留 >= 3 字符的（避免 "Son" 等短名误匹配）
        names = [n for n in names if n and len(n) >= 3]
        kws.append(names)
    return kws


def _load_country_names() -> dict:
    """国家 ID -> 展示名，用于相关性解释。"""
    global _COUNTRY_NAME_CACHE
    if _COUNTRY_NAME_CACHE is None:
        names = {}
        for c in store.list_countries():
            names[c["id"]] = c.get("name_cn") or c.get("name_en") or str(c["id"])
        _COUNTRY_NAME_CACHE = names
    return _COUNTRY_NAME_CACHE


def _contains_kw(text: str, kw: str) -> bool:
    """关键词命中：英文用词边界，中文/混合文本用子串。"""
    if not text or not kw:
        return False
    text_l = text.lower()
    kw_l = kw.lower().strip()
    if not kw_l:
        return False
    if all(ord(ch) < 128 for ch in kw_l):
        return re.search(r"(?<![a-z0-9])" + re.escape(kw_l) + r"(?![a-z0-9])", text_l) is not None
    return kw_l in text_l


def _hits(text: str, words: list[str]) -> list[str]:
    out = []
    for w in words:
        if w and _contains_kw(text, w):
            out.append(w)
    return list(dict.fromkeys(out))


def _country_mentions(text: str) -> set[int]:
    """粗略识别文本里出现的国家，用于发现第三方对阵污染。"""
    mentions = set()
    cmap = _load_country_keywords()
    for cid, words in cmap.items():
        if any(_contains_kw(text, w) for w in words):
            mentions.add(cid)
    return mentions


def _has_match_signal(text: str) -> bool:
    """是否像一场具体比赛，而不是泛世界杯文章。"""
    if not text:
        return False
    text_l = text.lower()
    return bool(
        re.search(r"\b(vs\.?|v\.?|versus|against)\b", text_l)
        or re.search(r"[\u4e00-\u9fa5a-zA-Z]+[ -]?\d+[ -]?\d+[ -]?[\u4e00-\u9fa5a-zA-Z]+", text_l)
        or any(s in text_l for s in ["对阵", "迎战", "击败", "不敌", "战胜", "战平"])
    )


def _is_generic_worldcup_title(title: str) -> bool:
    """明显泛用、容易被分到多场比赛的标题。"""
    title_l = (title or "").lower()
    generic_patterns = [
        "fixtures and results",
        "complete eastern time",
        "complete pacific time",
        "complete mountain time",
        "kickoff times",
        "tv channel",
        "how to watch",
        "fubo",
        "free trials",
        "subscription",
        "routes to the final",
        "top scorers",
        "records",
        "appearances by a player",
        "red cards in world cup history",
        "team of the day",
        "power rankings",
        "host the 2038 world cup",
        "qualification for the world cup knockout",
        "intra-group teams finish level",
    ]
    generic_cn = ["赛程", "积分榜", "午报", "早报", "盘点", "纪录", "集锦", "视频"]
    return any(p in title_l for p in generic_patterns) or any(p in (title or "") for p in generic_cn)


def _make_result(tier=None, score=0.0, label="已过滤", reason="未命中当前比赛核心信息"):
    return {"tier": tier, "score": score, "label": label, "reason": reason}


def generate_match_keywords(match: dict) -> dict:
    """
    生成比赛关键词集合，5 层 tier（中英文双语）
    match: dict with home_country_id, away_country_id, group_name, etc.
    优先使用 match 记录中的 home_en/home_cn/away_en/away_cn
    """
    home_id = match["home_country_id"]
    away_id = match["away_country_id"]
    cmap = _load_country_keywords()

    # 用 match 记录的中英文名（更准确），fallback 到 keywords
    home_en = match.get("home_en") or next((k for k in cmap.get(home_id, []) if k and k[0].isascii() and len(k) >= 3), "")
    away_en = match.get("away_en") or next((k for k in cmap.get(away_id, []) if k and k[0].isascii() and len(k) >= 3), "")
    home_cn = match.get("home_cn") or next((k for k in cmap.get(home_id, []) if k and not k[0].isascii()), "")
    away_cn = match.get("away_cn") or next((k for k in cmap.get(away_id, []) if k and not k[0].isascii()), "")

    # 用 match 记录中的国名（最准确），fallback 到 keywords 里最长的英文名
    hkw = [k for k in cmap.get(home_id, []) if k and k[0].isascii() and len(k) >= 3]
    akw = [k for k in cmap.get(away_id, []) if k and k[0].isascii() and len(k) >= 3]
    home_en_short = home_en or (max(hkw, key=len) if hkw else "")
    away_en_short = away_en or (max(akw, key=len) if akw else "")
    # 如果 match 里没有中文名，从 keywords 取
    if not home_cn:
        cn_kws = [k for k in cmap.get(home_id, []) if k and not k[0].isascii()]
        home_cn = cn_kws[0] if cn_kws else ""
    if not away_cn:
        cn_kws = [k for k in cmap.get(away_id, []) if k and not k[0].isascii()]
        away_cn = cn_kws[0] if cn_kws else ""

    # Tier A：直接相关（双方同时出现）—— 中英双语
    tier_A = []
    if home_en_short and away_en_short:
        tier_A.extend([
            f"{home_en_short} vs {away_en_short}",
            f"{home_en_short} v {away_en_short}",
            f"{home_en_short}-{away_en_short}",
            f"{home_en_short} {away_en_short}",
            f"{away_en_short} {home_en_short}",
        ])
    if home_cn and away_cn:
        tier_A.extend([
            f"{home_cn}vs{away_cn}",
            f"{home_cn}对{away_cn}",
            f"{home_cn}对阵{away_cn}",
            f"{home_cn}{away_cn}",
        ])
    # 去重
    tier_A = list(dict.fromkeys(tier_A))

    # Tier B：单方+世界杯语境 —— 中英双语
    en_ctx = ["World Cup", "preview", "team news", "squad", "lineup", "match preview", "prediction", "stats"]
    cn_ctx = ["世界杯", "前瞻", "阵容", "预测", "战报", "首发", "球队"]
    tier_B = []
    for team in [home_en_short, away_en_short]:
        if not team: continue
        for ctx in en_ctx[:6]:
            tier_B.append(f"{team} {ctx}")
    for team in [home_cn, away_cn]:
        if not team: continue
        for ctx in cn_ctx:
            tier_B.append(f"{team}{ctx}")

    # Tier C：双方球员 —— 控制在 3-5 个知名球员，避免过长
    tier_C = []
    for cid in [home_id, away_id]:
        player_list = _load_player_keywords(country_id=cid)
        count = 0
        for player_names in player_list:
            if count >= 8:  # 每队最多 8 个球员
                break
            # 取第一个名字（通常是主名）
            name = player_names[0] if player_names else ""
            if name and len(name) >= 3:
                tier_C.append(name)
                count += 1

    # Tier D：小组形势
    tier_D = []
    group = match.get("group_name")
    if group:
        tier_D.extend([
            f"Group {group}",
            f"小组{group}",
            f"{group}组",
            f"Group {group} standings",
        ])

    # Tier E：历史交锋（中英双语）
    tier_E = []
    if home_en_short and away_en_short:
        tier_E.extend([
            f"{home_en_short} {away_en_short} history",
            f"{home_en_short} {away_en_short} head to head",
        ])
    if home_cn and away_cn:
        tier_E.append(f"{home_cn}{away_cn}历史")
        tier_E.append(f"{home_cn}{away_cn}交锋")

    # 去重
    def dedupe(lst):
        seen = set()
        out = []
        for x in lst:
            xl = x.lower().strip()
            if xl and xl not in seen:
                seen.add(xl)
                out.append(x)
        return out

    return {
        "tier_A": dedupe(tier_A)[:10],
        "tier_B": dedupe(tier_B)[:20],
        "tier_C": dedupe(tier_C)[:40],
        "tier_D": dedupe(tier_D)[:8],
        "tier_E": dedupe(tier_E)[:6],
        "_home_only": dedupe(cmap.get(home_id, [])),
        "_away_only": dedupe(cmap.get(away_id, [])),
        "_home_id": home_id,
        "_away_id": away_id,
        "_home_label": home_cn or home_en_short,
        "_away_label": away_cn or away_en_short,
        "_group": group or "",
    }


def score_article_for_match(title: str, content: str, keywords: dict) -> dict:
    """
    返回文章对当前比赛的相关性。
    核心原则：标题优先；只靠正文宽泛出现两队不再足以入选。
    """
    title = title or ""
    body = (content or "")[:5000]
    search_text = f"{title}\n{body[:1500]}"

    home_id = keywords.get("_home_id")
    away_id = keywords.get("_away_id")
    current_ids = {home_id, away_id}
    home_label = keywords.get("_home_label") or "home"
    away_label = keywords.get("_away_label") or "away"

    home_title_hits = _hits(title, keywords.get("_home_only", []))
    away_title_hits = _hits(title, keywords.get("_away_only", []))
    home_body_hits = _hits(body, keywords.get("_home_only", []))
    away_body_hits = _hits(body, keywords.get("_away_only", []))

    title_mentions = _country_mentions(title)
    third_title_mentions = title_mentions - current_ids
    generic_title = _is_generic_worldcup_title(title)

    # 标题像另一场比赛时，直接挡掉。例：Brazil vs Haiti 不应进入 Morocco vs Haiti。
    if third_title_mentions and (home_title_hits or away_title_hits) and _has_match_signal(title):
        names = _load_country_names()
        third = ", ".join(names.get(cid, str(cid)) for cid in sorted(third_title_mentions))
        return _make_result(
            reason=f"标题像其他对阵，包含第三方球队：{third}"
        )
    if third_title_mentions and ((home_title_hits and not away_title_hits) or (away_title_hits and not home_title_hits)):
        names = _load_country_names()
        third = ", ".join(names.get(cid, str(cid)) for cid in sorted(third_title_mentions))
        return _make_result(
            reason=f"标题只命中当前一队，并聚焦第三方球队：{third}"
        )
    if third_title_mentions and not (home_title_hits or away_title_hits):
        names = _load_country_names()
        third = ", ".join(names.get(cid, str(cid)) for cid in sorted(third_title_mentions))
        return _make_result(
            reason=f"标题聚焦第三方球队：{third}"
        )

    # 泛用世界杯文章没有当前两队标题命中时，不进入比赛包。
    if generic_title and not (home_title_hits or away_title_hits):
        return _make_result(reason="泛世界杯/赛程/盘点类标题，未命中当前两队")

    # Tier A：标题明确当前对阵。
    for kw in keywords.get("tier_A", []):
        if _contains_kw(title, kw):
            return _make_result("A", 1.0, "直接对阵", f"标题命中当前对阵：{kw}")
    if home_title_hits and away_title_hits:
        return _make_result("A", 0.96, "直接对阵", f"标题同时命中：{home_label} / {away_label}")

    intent_signals = [
        "preview", "prediction", "predictions", "team news", "lineup", "lineups",
        "squad", "injury", "injuries", "odds", "best bets", "stats", "tactical",
        "analysis", "must win", "qualify", "standings", "前瞻", "预测", "阵容",
        "首发", "伤病", "出线", "形势", "分析", "数据", "赔率", "战"
    ]
    has_intent = any(s in title.lower() for s in intent_signals) or any(s in title for s in intent_signals)

    # Tier B：标题有当前一队 + 明确赛前/球队/形势语境。
    for kw in keywords.get("tier_B", []):
        if _contains_kw(title, kw):
            return _make_result("B", 0.82, "单队相关", f"标题命中赛前语境：{kw}")
    if (home_title_hits or away_title_hits) and has_intent:
        side = home_label if home_title_hits else away_label
        return _make_result("B", 0.78, "单队相关", f"标题命中当前球队和赛前语境：{side}")

    # 正文同时出现两队，只有在标题也至少命中一队时才算弱相关。
    if (home_body_hits and away_body_hits) and (home_title_hits or away_title_hits):
        return _make_result("B", 0.70, "弱相关", "正文同时提到两队，标题命中其中一队")

    # Tier C：当前两队球员只在标题里命中才收，避免正文长文误挂。
    for kw in keywords.get("tier_C", []):
        if _contains_kw(title, kw):
            return _make_result("C", 0.62, "球员相关", f"标题命中当前球队球员：{kw}")

    # Tier D：同组/积分形势，要求标题或正文同时出现当前球队。
    for kw in keywords.get("tier_D", []):
        if _contains_kw(title, kw) and (home_title_hits or away_title_hits):
            return _make_result("D", 0.48, "同组背景", f"标题命中小组语境：{kw}")
        if _contains_kw(search_text, kw) and (home_body_hits or away_body_hits):
            return _make_result("D", 0.42, "同组背景", f"正文命中小组语境：{kw}")

    # Tier E：历史交锋，要求正文里有明确 H2H 关键词。
    for kw in keywords.get("tier_E", []):
        if _contains_kw(search_text, kw):
            return _make_result("E", 0.58, "历史交锋", f"命中历史交锋：{kw}")

    if home_title_hits or away_title_hits:
        side = home_label if home_title_hits else away_label
        return _make_result("D", 0.40, "弱相关", f"仅标题命中当前球队：{side}")

    return _make_result()


def article_matches_tier(title: str, content: str, keywords: dict) -> str | None:
    """兼容旧调用：只返回 tier。"""
    return score_article_for_match(title, content, keywords).get("tier")


if __name__ == "__main__":
    # 自检：取第一场未来比赛
    matches = store.list_upcoming_matches(days=4) if hasattr(store, "list_upcoming_matches") else []
    if not matches:
        # 兜底：手动取
        with store.conn_ctx() as conn:
            rows = conn.execute("""
                SELECT * FROM matches WHERE kickoff_at >= datetime('now')
                ORDER BY kickoff_at LIMIT 1
            """).fetchall()
            matches = [dict(r) for r in rows]
    if matches:
        kws = generate_match_keywords(matches[0])
        print(json.dumps(kws, ensure_ascii=False, indent=2))
