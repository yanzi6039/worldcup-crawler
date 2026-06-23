"""
爬虫状态管理器：线程安全 + 文件持久化 + SSE 推送
所有页面共享同一份状态数据，切换页面不丢失
"""
import os
import json
import time
import threading
import logging
from queue import Queue

log = logging.getLogger("crawl_status")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_FILE = os.path.join(BASE_DIR, "data", "crawl_status.json")


class CrawlStatus:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: list[Queue] = []
        self._data = self._load()
        # 启动时清理陈旧状态：若上次爬取超过 10 分钟还标记为 running，
        # 必然是进程异常退出留下的脏状态（finish_crawl 未调用）
        if self._data.get("running"):
            started_at = self._data.get("started_at") or 0
            if time.time() - started_at > 600:  # 10 分钟
                log.warning(f"检测到陈旧 running 状态（启动于 {time.ctime(started_at)}），自动重置")
                self._data["running"] = False
                self._data["current_source"] = ""
                self._data["started_at"] = None
                self._save()

    def _load(self):
        try:
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE) as f:
                    return json.load(f)
        except:
            pass
        return {"running": False, "started_at": None, "current_source": "", "log": [], "sources_done": [], "total_found": 0, "total_new": 0}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
            with open(STATUS_FILE, "w") as f:
                json.dump(self._data, f, ensure_ascii=False)
        except:
            pass

    def _notify(self):
        msg = json.dumps(self._data, ensure_ascii=False)
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(msg)
            except:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)

    def subscribe(self) -> Queue:
        q = Queue(maxsize=50)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        try:
            self._subscribers.remove(q)
        except:
            pass

    def log_event(self, msg: str, level: str = "info"):
        with self._lock:
            self._data["log"].append({
                "time": time.strftime("%m-%d %H:%M:%S"),
                "msg": msg,
                "level": level,
            })
            if len(self._data["log"]) > 120:
                self._data["log"] = self._data["log"][-100:]
            self._save()
            self._notify()

    def start_crawl(self, source_name: str = ""):
        with self._lock:
            self._data["running"] = True
            self._data["started_at"] = time.time()
            if source_name:
                self._data["current_source"] = source_name
            self._data["sources_done"] = []
            self._data["total_found"] = 0
            self._data["total_new"] = 0
            self._save()
            self._notify()

    def source_start(self, name: str):
        with self._lock:
            self._data["current_source"] = name
            self._save()
            self._notify()

    def source_done(self, name: str, found: int, new: int, ok: bool = True):
        with self._lock:
            self._data["total_found"] += found
            self._data["total_new"] += new
            self._data["sources_done"].append({
                "name": name,
                "found": found,
                "new": new,
                "ok": ok,
            })
            self._data["current_source"] = ""
            self._save()
            self._notify()

    def finish_crawl(self):
        with self._lock:
            self._data["running"] = False
            self._data["current_source"] = ""
            self._data["started_at"] = None
            self._save()
            self._notify()

    def status(self) -> dict:
        with self._lock:
            return dict(self._data)

    def is_running(self) -> bool:
        return self._data.get("running", False)


# 全局单例
crawl_status = CrawlStatus()
