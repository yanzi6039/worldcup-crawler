"""
时间显示工具：所有时间统一转北京时间（UTC+8）显示
SQLite 存的 kickoff_at 多为 UTC（无时区后缀）或带时区 ISO，
统一解析后转 Asia/Shanghai
"""
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))


def to_beijing(iso_str: str, fmt: str = "%m-%d %H:%M") -> str:
    """
    把 ISO 时间字符串转北京时间显示。
    支持：
      - '2026-06-22 20:00:00'（无时区，按 UTC 处理）
      - '2026-06-22T20:00:00+00:00'
      - '2026-06-22T20:00:00Z'
      - 'Thu, 22 Jun 2026 14:00:00 +0000'
    """
    if not iso_str or not isinstance(iso_str, str):
        return ""
    s = iso_str.strip()
    # 尝试多种格式
    parsed = None
    for fmt_try in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%a, %d %b %Y %H:%M:%S %z",   # RFC822 (RSS)
        "%d %b %Y %H:%M:%S %z",
    ):
        try:
            parsed = datetime.strptime(s[:30], fmt_try)
            break
        except Exception:
            continue
    if parsed is None:
        return s[:16]  # 兜底截断
    # 没时区信息的按 UTC
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    # 转北京时间
    bj = parsed.astimezone(BEIJING_TZ)
    return bj.strftime(fmt)


def beijing_day(iso_str: str) -> str:
    """返回北京时间的日期（YYYY-MM-DD），用于按日分组"""
    return to_beijing(iso_str, "%Y-%m-%d")


def beijing_time_label(iso_str: str) -> str:
    """返回 'HH:MM' 北京时间"""
    return to_beijing(iso_str, "%H:%M")


def beijing_relative_day(iso_str: str) -> str:
    """返回相对标签：今天/明天/后天/N天后"""
    if not iso_str:
        return "未定"
    bj_day = beijing_day(iso_str)
    now_bj = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    try:
        d1 = datetime.strptime(bj_day, "%Y-%m-%d")
        d2 = datetime.strptime(now_bj, "%Y-%m-%d")
        diff = (d1 - d2).days
    except Exception:
        return bj_day
    if diff == 0: return "今天"
    if diff == 1: return "明天"
    if diff == 2: return "后天"
    if diff == 3: return "大后天"
    if diff > 0: return f"{diff}天后"
    return bj_day
