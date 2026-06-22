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
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from db import store

log = logging.getLogger("match_keywords")

# 缓存国家关键词
_COUNTRY_KW_CACHE = None
_PLAYER_KW_CACHE = None


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
    }


def article_matches_tier(title: str, content: str, keywords: dict) -> str | None:
    """
    判断一篇文章命中哪一层 tier，返回 'A'/'B'/'C'/'D'/'E' 或 None
    标题权重更高，正文取前 3000 字
    """
    title_l = (title or "").lower()
    body_l = (content or "")[:5000].lower()

    home_words = [k.lower() for k in keywords.get("_home_only", [])]
    away_words = [k.lower() for k in keywords.get("_away_only", [])]
    home_in_title = any(k in title_l for k in home_words)
    away_in_title = any(k in title_l for k in away_words)
    home_in_body = any(k in body_l for k in home_words)
    away_in_body = any(k in body_l for k in away_words)

    # Tier A：标题含 "X vs Y" 关键词，或标题含双方国名
    for kw in keywords["tier_A"]:
        if kw.lower() in title_l:
            return "A"
    if home_in_title and away_in_title:
        return "A"

    # 正文含双方国名 → 只能是 B 级（正文宽泛，容易误匹配）
    if home_in_body and away_in_body:
        return "B"

    # Tier B：标题含单方+语境词（中英双语）
    for kw in keywords["tier_B"]:
        if kw.lower() in title_l:
            return "B"
    # 标题含单方国名 + 含世界杯通用语境词
    wc_signals = ["world cup", "世界杯", "前瞻", "预测", "preview", "prediction", "squad", "阵容", "战报"]
    if (home_in_title or away_in_title) and any(s in title_l for s in wc_signals):
        return "B"

    # Tier C：球员名在标题或正文
    for kw in keywords["tier_C"]:
        kwl = kw.lower()
        if len(kwl) < 3:
            continue
        if kwl in title_l:
            return "C"
    # 球员在正文（只有标题没命中时才扫正文，避免过多 C 级 match）
    for kw in keywords["tier_C"]:
        kwl = kw.lower()
        if len(kwl) < 3:
            continue
        if kwl in body_l:
            return "C"

    # Tier D：小组名
    for kw in keywords["tier_D"]:
        if kw.lower() in title_l or kw.lower() in body_l:
            return "D"

    # Tier E：H2H（正文中）
    for kw in keywords["tier_E"]:
        if kw.lower() in body_l:
            return "E"

    return None


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
