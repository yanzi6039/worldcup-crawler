"""
世界杯赛程构建器（取代 schedule_wikipedia.py）

数据流：
  1. data/schedule_local.json     —— 人工校准的权威种子（北京时间）
  2. openfootball/worldcup.json   —— GitHub 全量 104 场（UTC 偏移）
  3. ESPN scoreboard API          —— 回填比分/状态（不新建比赛）

存储约定：kickoff_at 一律存 UTC（无后缀，'YYYY-MM-DD HH:MM:SS'）。
显示层 web/timezone_utils.to_beijing 会自动 +8 转北京时间。

合并去重：按 (home_country_id, away_country_id, date(kickoff_at)) 唯一。
本地数据优先：同一场比赛若本地已存在，不覆盖。
"""
import os
import sys
import json
import re
import time
import random
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from base_scraper import fetch_requests, _throttle, log
from db import store
from config import DATA_DIR

LOCAL_JSON = os.path.join(DATA_DIR, "schedule_local.json")
OPENFOOTBALL_URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?dates=2026"

# openfootball 国家名 → 本库 name_en
COUNTRY_ALIASES = {
    "United States": "USA",
    "USA": "USA",
    "Korea Republic": "South Korea",
    "South Korea": "South Korea",
    "IR Iran": "Iran",
    "Iran": "Iran",
    "Czech Republic": "Czechia",
    "Czechia": "Czechia",
    "DR Congo": "DR Congo",
    "Congo DR": "DR Congo",
    "Cape Verde": "Cape Verde",
    "Cabo Verde": "Cape Verde",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
    "Ivory Coast": "Ivory Coast",
    "Côte d'Ivoire": "Ivory Coast",
    "Curacao": "Curacao",
    "Curaçao": "Curacao",
}

# round 名翻译映射（openfootball Matchday N → 中文轮次）
def _translate_round(round_str: str, group: str) -> tuple[str, int]:
    """返回 (tournament_round 中文, match_day 数字)"""
    m = re.search(r"Matchday\s+(\d+)", round_str or "")
    day = int(m.group(1)) if m else 0
    # Matchday 1-2 → 第1轮, 3-7 → 第2轮, 8-13 → 第2轮(跨组), 14+ → 第3轮
    # 实际世界杯规则：每组 4 队互踢，共 12 组 24 场轮次 = 72 场小组赛
    if day <= 4:
        return ("小组赛第1轮", 1)
    elif day <= 7:
        return ("小组赛第2轮", 2)
    elif day <= 13:
        return ("小组赛第2轮", 2)
    else:
        return ("小组赛第3轮", 3)


def _country_id_map() -> dict:
    """国家名（小写）→ id，含中文 + keywords 别名"""
    with store.conn_ctx() as conn:
        cur = conn.execute("SELECT id, name_en, name_cn, keywords FROM countries")
        cmap = {}
        for r in cur.fetchall():
            for k in (r["name_en"], r["name_cn"]):
                if k:
                    cmap[k.lower()] = r["id"]
            # keywords 字段：["库拉索","Curaçao","Curacao",...]
            if r["keywords"]:
                try:
                    for kw in json.loads(r["keywords"]):
                        if kw:
                            cmap[kw.lower()] = r["id"]
                except (json.JSONDecodeError, TypeError):
                    pass
        return cmap


# ============ 1. 本地种子 ============

def load_local() -> list[dict]:
    """读取人工校准的本地赛程（北京时间）→ 转 UTC 存储"""
    if not os.path.exists(LOCAL_JSON):
        log.warning(f"  ⚠ 本地赛程不存在: {LOCAL_JSON}")
        return []
    with open(LOCAL_JSON, encoding="utf-8") as f:
        data = json.load(f)
    matches = []
    for m in data.get("matches", []):
        # 北京时间 → UTC（减 8 小时）
        try:
            dt_bj = datetime.strptime(m["kickoff_beijing"], "%Y-%m-%d %H:%M:%S")
            dt_utc = dt_bj - timedelta(hours=8)
            kickoff_utc = dt_utc.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            kickoff_utc = m["kickoff_beijing"]
        matches.append({
            "home_en": m["home_en"],
            "away_en": m["away_en"],
            "home_cn": m.get("home_cn", ""),
            "away_cn": m.get("away_cn", ""),
            "kickoff_at": kickoff_utc,
            "stage": f"Group {m.get('group', '')}".strip(),
            "venue": m.get("venue", ""),
            "group": m.get("group", ""),
            "round": m.get("round", ""),
            "tournament_round": m.get("round", ""),
            "home_score": None,
            "away_score": None,
            "source": "local",
        })
    log.info(f"  ✓ 本地种子: {len(matches)} 场 (北京时间→UTC)")
    return matches


# ============ 2. openfootball GitHub ============

def _parse_offset_and_convert(date_str: str, time_str: str) -> str:
    """
    把 '2026-06-11' + '13:00 UTC-6' 转 UTC 字符串 '2026-06-11 19:00:00'
    （存储约定：统一存 UTC，显示层 to_beijing 再 +8）
    """
    if not time_str:
        return f"{date_str} 00:00:00"
    m = re.match(r"(\d{1,2}):(\d{2})\s*UTC\s*([+-]?)\s*(\d+)", time_str.strip())
    if not m:
        return f"{date_str} 00:00:00"
    hh = int(m.group(1))
    mm = int(m.group(2))
    sign_str = m.group(3) or "-"
    offset = int(m.group(4))
    utc_offset = offset if sign_str == "+" else -offset
    # 当地时间 → UTC：local - offset
    total_min = hh * 60 + mm - utc_offset * 60
    dt = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(minutes=total_min)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def fetch_openfootball() -> list[dict]:
    """从 openfootball GitHub 拉全量赛程（104 场）"""
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    log.info(f"  ↓ openfootball: {OPENFOOTBALL_URL}")
    try:
        r = requests.get(OPENFOOTBALL_URL, timeout=30, verify=False,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            log.warning(f"  ✗ openfootball HTTP {r.status_code}")
            return []
        data = r.json()
    except Exception as e:
        log.warning(f"  ✗ openfootball 失败: {e}")
        return []

    matches = []
    for m in data.get("matches", []):
        team1 = COUNTRY_ALIASES.get(m.get("team1", "").strip(), m.get("team1", "").strip())
        team2 = COUNTRY_ALIASES.get(m.get("team2", "").strip(), m.get("team2", "").strip())
        kickoff = _parse_offset_and_convert(m.get("date", ""), m.get("time", ""))
        score = m.get("score") or {}
        ft = score.get("ft") or [None, None]
        group_raw = m.get("group", "")  # "Group A"
        group_letter = group_raw.replace("Group ", "").strip()
        tour_round, match_day = _translate_round(m.get("round", ""), group_letter)
        # 淘汰赛 round 形如 "Round of 32" 直接保留
        if "Group" not in group_raw and m.get("round"):
            tour_round = m.get("round")

        matches.append({
            "home_en": team1,
            "away_en": team2,
            "kickoff_at": kickoff,
            "stage": group_raw or m.get("round", ""),
            "venue": m.get("ground", ""),
            "group": group_letter if "Group" in group_raw else "",
            "round": m.get("round", ""),
            "tournament_round": tour_round,
            "match_day": match_day,
            "home_score": ft[0],
            "away_score": ft[1],
            "source": "openfootball",
        })
    log.info(f"  ✓ openfootball: {len(matches)} 场")
    return matches


# ============ 3. ESPN 比分回填 ============

def fetch_espn_scores() -> int:
    """从 ESPN 拉所有比赛比分/状态，回填到现有 matches（按队+日期匹配）。
    返回更新的比赛数。"""
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    log.info(f"  ↓ ESPN scoreboard")
    try:
        r = requests.get(ESPN_URL, timeout=30, verify=False,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            log.warning(f"  ✗ ESPN HTTP {r.status_code}")
            return 0
        data = r.json()
    except Exception as e:
        log.warning(f"  ✗ ESPN 失败: {e}")
        return 0

    espn_matches = []
    for ev in data.get("events", []):
        try:
            comp = ev["competitions"][0]
            dt_utc = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
            home = next(t for t in comp["competitors"] if t.get("homeAway") == "home")
            away = next(t for t in comp["competitors"] if t.get("homeAway") == "away")
            espn_matches.append({
                "home_en": COUNTRY_ALIASES.get(home["team"].get("displayName", ""),
                                                home["team"].get("displayName", "")),
                "away_en": COUNTRY_ALIASES.get(away["team"].get("displayName", ""),
                                                away["team"].get("displayName", "")),
                "kickoff_at": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "home_score": int(home.get("score")) if home.get("score") else None,
                "away_score": int(away.get("score")) if away.get("score") else None,
                "status": _espn_status(ev.get("status", {}).get("type", {}).get("name", "")),
            })
        except Exception as e:
            log.debug(f"  ESPN parse skip: {e}")
            continue

    # 回填
    updated = 0
    cmap = _country_id_map()
    with store.conn_ctx() as conn:

        for em in espn_matches:
            home_id = cmap.get(em["home_en"].lower())
            away_id = cmap.get(em["away_en"].lower())
            if not home_id or not away_id:
                continue
            row = conn.execute("""
                SELECT id FROM matches
                WHERE home_country_id=? AND away_country_id=?
                  AND date(kickoff_at) = date(?)
                LIMIT 1
            """, (home_id, away_id, em["kickoff_at"])).fetchone()
            if row:
                conn.execute("""
                    UPDATE matches SET home_score=?, away_score=?, status=?
                    WHERE id=?
                """, (em["home_score"], em["away_score"], em["status"], row["id"]))
                updated += 1
    log.info(f"  ✓ ESPN 回填: {updated} 场")
    return updated


def _espn_status(name: str) -> str:
    return {
        "STATUS_SCHEDULED": "scheduled",
        "STATUS_IN_PROGRESS": "live",
        "STATUS_FINAL": "ended",
    }.get(name, "scheduled")


# ============ 4. 合并去重 + 入库 ============

def upsert_matches(matches: list[dict]) -> tuple[int, int, int]:
    """返回 (新增, 更新, 跳过)"""
    added, updated, skipped = 0, 0, 0
    cmap = _country_id_map()
    with store.conn_ctx() as conn:

        for m in matches:
            home_id = cmap.get(m["home_en"].lower()) or cmap.get(m.get("home_cn", "").lower())
            away_id = cmap.get(m["away_en"].lower()) or cmap.get(m.get("away_cn", "").lower())
            if not home_id or not away_id:
                log.warning(f"    ⚠ 国家未识别: {m['home_en']} / {m['away_en']} (src={m.get('source')})")
                skipped += 1
                continue

            existing = conn.execute("""
                SELECT id FROM matches
                WHERE home_country_id=? AND away_country_id=?
                  AND date(kickoff_at) = date(?)
                LIMIT 1
            """, (home_id, away_id, m["kickoff_at"])).fetchone()

            if existing:
                # 已存在：仅当本地数据来时才覆盖（金标准），其他源只补比分
                if m.get("source") == "local":
                    conn.execute("""
                        UPDATE matches SET kickoff_at=?, venue=?, group_name=?,
                                           tournament_round=?, stage=?
                        WHERE id=?
                    """, (m["kickoff_at"], m["venue"], m.get("group", ""),
                          m.get("tournament_round", ""), m["stage"], existing["id"]))
                    updated += 1
                # 其他源若已有比赛且新数据有比分，更新比分
                elif m.get("home_score") is not None:
                    conn.execute("""
                        UPDATE matches SET home_score=?, away_score=?, status='ended'
                        WHERE id=? AND home_score IS NULL
                    """, (m["home_score"], m["away_score"], existing["id"]))
            else:
                status = "ended" if (m.get("home_score") is not None) else "scheduled"
                conn.execute("""
                    INSERT INTO matches (home_country_id, away_country_id, kickoff_at,
                                         stage, venue, group_name, tournament_round,
                                         match_day, home_score, away_score, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (home_id, away_id, m["kickoff_at"], m["stage"], m["venue"],
                      m.get("group", ""), m.get("tournament_round", ""),
                      m.get("match_day"), m.get("home_score"), m.get("away_score"),
                      status))
                added += 1
    return added, updated, skipped


def clear_matches():
    """清空赛程表（级联删除 news_match_links，不动 news）"""
    with store.conn_ctx() as conn:
        conn.execute("DELETE FROM matches")
    log.info("  ✓ matches 表已清空")


def build_all(with_espn: bool = True) -> dict:
    """完整构建赛程：本地 + openfootball，可选 ESPN 回填比分。
    返回统计 dict。"""
    log.info("=" * 60)
    log.info("▶ 构建世界杯赛程（本地 + openfootball）")

    local = load_local()
    remote = fetch_openfootball()

    # 去重：同 (home, away, date) 时优先用 local 覆盖 remote
    seen = {}
    for m in remote:
        key = (m["home_en"].lower(), m["away_en"].lower(), m["kickoff_at"][:10])
        seen[key] = m
    for m in local:
        # 反向也尝试匹配（home/away 顺序可能不同）
        key1 = (m["home_en"].lower(), m["away_en"].lower(), m["kickoff_at"][:10])
        key2 = (m["away_en"].lower(), m["home_en"].lower(), m["kickoff_at"][:10])
        if key1 in seen or key2 in seen:
            # 用 local 数据覆盖（金标准）
            seen.get(key1, seen.get(key2)).update({
                "kickoff_at": m["kickoff_at"],
                "venue": m["venue"],
                "group": m.get("group", ""),
                "tournament_round": m.get("tournament_round", ""),
                "stage": m["stage"],
                "source": "local",
            })
        else:
            seen[key1] = m

    merged = list(seen.values())
    log.info(f"  合并去重后: {len(merged)} 场")

    added, updated, skipped = upsert_matches(merged)

    # ESPN 回填比分
    espn_updated = 0
    if with_espn:
        try:
            espn_updated = fetch_espn_scores()
        except Exception as e:
            log.warning(f"  ESPN 回填异常（不影响赛程构建）: {e}")

    # 持久化一份合并后的 JSON 备份
    out = os.path.join(DATA_DIR, "schedule.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, default=str)

    stats = {
        "total": len(merged),
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "espn_updated": espn_updated,
    }
    log.info(f"  ✓ 完成: 新增 {added} / 更新 {updated} / 跳过 {skipped} / ESPN {espn_updated}")

    # 统计
    with store.conn_ctx() as conn:
        for label, cond in [
            ("已完赛", "status='ended'"),
            ("未来 4 天", "kickoff_at >= datetime('now') AND kickoff_at <= datetime('now','+4 days')"),
            ("未来 1-3 天", "kickoff_at >= datetime('now') AND kickoff_at <= datetime('now','+3 days')"),
        ]:
            cnt = conn.execute(f"SELECT COUNT(*) FROM matches WHERE {cond}").fetchone()[0]
            log.info(f"  📅 {label}: {cnt} 场")
    return stats


def main(rebuild: bool = False):
    if rebuild:
        clear_matches()
    build_all(with_espn=True)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--rebuild", action="store_true", help="清空 matches 表后重建")
    args = p.parse_args()
    main(rebuild=args.rebuild)
