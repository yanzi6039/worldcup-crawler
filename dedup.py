"""
新闻去重：URL + 标题 + 内容 minhash 三层
- URL hash：完全相同 URL
- 标题归一化 hash：标题相同（忽略大小写/标点）
- 内容 Bottom-K MinHash：正文相似度 > 0.85 视为重复
"""
import os
import sys
import re
import json
import hashlib
import logging
from typing import Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

log = logging.getLogger("dedup")

# 内存缓存：signature 列表（启动时从 DB 加载）
_SIG_CACHE: Optional[list[set]] = None
_TITLE_HASH_CACHE: Optional[set] = None
_URL_HASH_CACHE: Optional[set] = None


def title_key(t: str) -> str:
    """标题归一化：小写 + 去标点 + 折叠空白"""
    if not t:
        return ""
    t = re.sub(r"[^\w\s\u4e00-\u9fa5]", "", t.lower())
    t = re.sub(r"\s+", " ", t).strip()
    # 截断（防止超长标题比较）
    return t[:200]


def title_hash(t: str) -> str:
    return hashlib.md5(title_key(t).encode()).hexdigest()


def url_hash(url: str) -> str:
    # 复用 store 的实现
    return hashlib.md5(url.encode("utf-8")).hexdigest()


_SHINGLE_RE = re.compile(r"\w+", re.UNICODE)


def content_signature(text: str, k: int = 64, shingle_size: int = 5) -> list[int]:
    """
    Bottom-K MinHash 签名（k=64 个最小 hash）
    - shingle_size=5：5 个连续词为一个 shingle
    - 对中英文都适用（\w+ 能匹配中文字符）
    """
    if not text:
        return []
    words = _SHINGLE_RE.findall(text.lower())
    if len(words) < shingle_size:
        # 太短，直接 hash 全文
        return [int(hashlib.md5(text.encode()).hexdigest()[:16], 16)]
    hashes = []
    for i in range(len(words) - shingle_size + 1):
        sh = " ".join(words[i:i + shingle_size])
        h = int(hashlib.md5(sh.encode()).hexdigest()[:16], 16)
        hashes.append(h)
    hashes.sort()
    return hashes[:k]


def signature_to_str(sig: list[int]) -> str:
    """存 DB 用：逗号分隔"""
    return ",".join(str(h) for h in sig)


def signature_from_str(s: str) -> set[int]:
    if not s:
        return set()
    try:
        return {int(x) for x in s.split(",") if x}
    except Exception:
        return set()


def jaccard(sig1: set, sig2: set) -> float:
    if not sig1 or not sig2:
        return 0.0
    inter = len(sig1 & sig2)
    union = len(sig1 | sig2)
    return inter / union if union else 0.0


# ============ 缓存加载 ============

def _load_caches():
    """从 DB 加载所有 hash 缓存（首次调用时）"""
    global _SIG_CACHE, _TITLE_HASH_CACHE, _URL_HASH_CACHE
    if _SIG_CACHE is not None:
        return
    from db import store
    _SIG_CACHE = []
    _TITLE_HASH_CACHE = set()
    _URL_HASH_CACHE = set()
    with store.conn_ctx() as conn:
        # 只载有 signature 的
        for r in conn.execute("SELECT url_hash, title_hash, signature FROM news").fetchall():
            if r["url_hash"]:
                _URL_HASH_CACHE.add(r["url_hash"])
            if r["title_hash"]:
                _TITLE_HASH_CACHE.add(r["title_hash"])
            if r["signature"]:
                sig = signature_from_str(r["signature"])
                if sig:
                    _SIG_CACHE.append(sig)
    log.info(f"  dedup cache loaded: {_len_or_0(_URL_HASH_CACHE)} urls, "
             f"{_len_or_0(_TITLE_HASH_CACHE)} titles, {len(_SIG_CACHE)} sigs")


def _len_or_0(s):
    return len(s) if s else 0


def reset_cache():
    """重置缓存（测试/重建用）"""
    global _SIG_CACHE, _TITLE_HASH_CACHE, _URL_HASH_CACHE
    _SIG_CACHE = None
    _TITLE_HASH_CACHE = None
    _URL_HASH_CACHE = None


# ============ 主接口 ============

def find_duplicate(url: str, title: str, content: str,
                   threshold: float = 0.85) -> Optional[str]:
    """
    返回重复类型 'url' / 'title' / 'content' 或 None（非重复）
    """
    _load_caches()

    # 1. URL hash
    uh = url_hash(url)
    if uh in _URL_HASH_CACHE:
        return "url"

    # 2. 标题归一化 hash
    th = title_hash(title) if title else None
    if th and th in _TITLE_HASH_CACHE:
        return "title"

    # 3. 内容 minhash
    if content and len(content) > 100:
        sig = set(content_signature(content))
        if sig:
            for existing in _SIG_CACHE:
                if jaccard(sig, existing) >= threshold:
                    return "content"

    return None


def remember(url: str, title: str, content: str) -> tuple[str, str, str]:
    """
    记录到缓存（入库后调用）。返回 (url_hash, title_hash, signature_str) 给 DB 存
    """
    _load_caches()
    uh = url_hash(url)
    th = title_hash(title) if title else ""
    sig_str = ""
    if content and len(content) > 100:
        sig = content_signature(content)
        sig_str = signature_to_str(sig)
        _SIG_CACHE.append(set(sig) if isinstance(sig, list) else sig)
    _URL_HASH_CACHE.add(uh)
    if th:
        _TITLE_HASH_CACHE.add(th)
    return uh, th, sig_str


if __name__ == "__main__":
    # 自检
    logging.basicConfig(level=logging.INFO)
    t1 = "Spain 4-0 Saudi Arabia: World Cup report"
    t2 = "Spain 4-0 Saudi Arabia: World Cup report!"  # 标点不同
    t3 = "Completely different title"
    print(f"title_hash(t1) == title_hash(t2): {title_hash(t1) == title_hash(t2)}")  # True
    print(f"title_hash(t1) == title_hash(t3): {title_hash(t1) == title_hash(t3)}")  # False

    c1 = "Lamine Yamal scored his first World Cup goal as Spain beat Saudi Arabia 4-0."
    c2 = "Lamine Yamal scored his first World Cup goal as Spain beat Saudi Arabia 4-0 in Atlanta."
    c3 = "Completely different content about another match."
    s1, s2, s3 = set(content_signature(c1)), set(content_signature(c2)), set(content_signature(c3))
    print(f"jaccard(c1,c2) = {jaccard(s1, s2):.2f}  (should be high)")
    print(f"jaccard(c1,c3) = {jaccard(s1, s3):.2f}  (should be low)")
