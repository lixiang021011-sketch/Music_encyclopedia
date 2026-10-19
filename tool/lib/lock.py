"""One build at a time: the watcher in serve.py and a manual rebuild.cmd share this lock file."""
import json, os, time

STALE_SECONDS = 30 * 60        # a build never takes this long; an older lock was left by a crashed process


class Busy(Exception):
    pass


class BuildLock:
    def __init__(self, path, wait=0):
        self.path, self.wait = path, wait

    def __enter__(self):
        t0 = time.time()
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(dict(pid=os.getpid(), since=time.time()), f)
                return self
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                except OSError:
                    continue                      # released between the two calls
                if age > STALE_SECONDS:
                    try:
                        os.remove(self.path); continue
                    except OSError:                   # cannot delete here (e.g. a folder without delete rights): take it over
                        with open(self.path, "w", encoding="utf-8") as f:
                            json.dump(dict(pid=os.getpid(), since=time.time(), takenOver=True), f)
                        return self
                if time.time() - t0 >= self.wait:
                    raise Busy(f"另一个构建正在进行（锁文件 {self.path}）")
                time.sleep(1)

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            try: os.utime(self.path, (0, 0))        # could not delete: mark it stale so the next build takes it over
            except OSError: pass
