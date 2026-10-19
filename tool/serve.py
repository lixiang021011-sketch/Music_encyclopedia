"""本机打开例句库：起一个只监听 127.0.0.1 的小服务器，页面和原曲音频从同一处提供，打开就能播放片段。

    python serve.py              # 默认端口 8765，并自动打开浏览器
    python serve.py --port 9000 --no-open
    python serve.py --no-watch   # 不自动重建（仍然每 20 秒检查一次，页面顶部会提示索引是否过时）

自动重建只针对 publish.json 里已发布的歌：它们在导出里有变化、而且连续两次检查都一样（导出已经写完），
或者 config.json / publish.json / 构建程序改过，才重建。导出里新出现的歌只计入"待审核"，不会自动进例句库。
检查结果写在网站文件夹的 _status.json，页面顶部据此显示"和当前导出一致"或"索引过时"。

只放行两类路径：例句库网站文件夹里的文件，和已发布歌曲的 workbench_data_baseline/<歌>/sources/audio/master.* 。
其他文件一律 404。支持 Range 请求（音频跳转播放需要）。只用标准库。
"""
import argparse, json, mimetypes, os, re, sys, threading, time, traceback, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote, urlsplit, quote

HERE = os.path.dirname(os.path.abspath(__file__))
mimetypes.add_type("audio/ogg", ".ogg"); mimetypes.add_type("audio/flac", ".flac"); mimetypes.add_type("application/json", ".json")


def load_cfg():
    cfg = {}
    for fn in ("config.json", "config.local.json"):          # the local file (not in the repo) overrides
        try:
            with open(os.path.join(HERE, fn), encoding="utf-8") as f: cfg.update(json.load(f))
        except (OSError, ValueError): pass
    return cfg


def resolve(p):
    return os.path.normcase(os.path.realpath(p if os.path.isabs(p) else os.path.join(HERE, p)))


_pub = {"mtime": None, "songs": set()}


def published_songs(site):
    """songs of the current build (from _snapshot.json, re-read when it changes)"""
    p = os.path.join(site, "_snapshot.json")
    try: m = os.path.getmtime(p)
    except OSError: return set()
    if m != _pub["mtime"]:
        try:
            with open(p, encoding="utf-8") as f: snap = json.load(f); _pub["songs"] = {os.path.normcase(n) for n in snap.get("indexed", snap.get("published", {}))}
            _pub["mtime"] = m
        except (OSError, ValueError): pass
    return _pub["songs"]


def make_handler(root, site, baseline):
    audio_re = re.compile(r"^[^/\\]+[/\\]sources[/\\]audio[/\\]master\.(ogg|flac|wav|mp3|m4a)$", re.I)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def allowed(self, fs):
            if fs.startswith(site + os.sep) or fs == site:
                return not any(x.startswith(".") for x in re.split(r"[/\\]", os.path.relpath(fs, site)) if x != ".")   # .staging, .build.lock
            if fs.startswith(baseline + os.sep):
                rel = os.path.relpath(fs, baseline)
                return bool(audio_re.match(rel)) and re.split(r"[/\\]", rel)[0] in published_songs(site)
            return False

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            fs = resolve(os.path.join(root, path.lstrip("/")))
            if os.path.isdir(fs):
                if not path.endswith("/"):
                    self.send_response(301); self.send_header("Location", quote(path) + "/"); self.end_headers(); return
                fs = os.path.join(fs, "index.html")
            if not self.allowed(fs) or not os.path.isfile(fs):
                self.send_error(404); return
            size = os.path.getsize(fs); start, end = 0, size - 1
            rng = self.headers.get("Range")
            m = re.match(r"bytes=(\d*)-(\d*)$", rng or "")
            if m and (m.group(1) or m.group(2)):
                if m.group(1): start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else size - 1
                else: start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end: self.send_error(416); return
                self.send_response(206); self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            ctype = mimetypes.guess_type(fs)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("json"): ctype += "; charset=utf-8"
            self.send_header("Content-Type", ctype); self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1)); self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            with open(fs, "rb") as f:
                f.seek(start); left = end - start + 1
                try:
                    while left > 0:
                        chunk = f.read(min(1 << 16, left))
                        if not chunk: break
                        self.wfile.write(chunk); left -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass          # the audio element cancels requests while seeking
    return H


def watch(base, out, cfg, rebuild=True, every=20):
    """check the export every `every` seconds, write _status.json, rebuild when the published inputs changed and settled"""
    sys.path.insert(0, HERE)
    import build_library as bl
    from lib.lock import Busy
    last = None
    while True:
        try:
            cfg = bl.load_cfg()
            st = bl.status(base, out, cfg)
            if st["stale"] and rebuild and st["inputKey"] == last:
                print(time.strftime("\n[%H:%M:%S] ") + "，".join(st["reasons"]) + "：重建例句库…", flush=True)
                bl.run(base, out, cfg, wait=0)
                st = bl.status(base, out, cfg)
                print("刷新浏览器页面即可看到新版。", flush=True)
            last = st["inputKey"]
            bl.save_json(os.path.join(out, "_status.json"), {k: v for k, v in st.items() if k != "inputKey"}, pretty=True)
        except Busy:
            pass                                   # a manual rebuild is running; check again next time
        except SystemExit as e:
            print("重建没有完成：", e, flush=True)
        except Exception:
            traceback.print_exc()
        time.sleep(every)


def main():
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    cfg = load_cfg()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=cfg.get("port", 8765))
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--no-watch", action="store_true")
    a = ap.parse_args()
    site, baseline = resolve(cfg.get("out", "../site")), resolve(cfg["baseline"])
    root = os.path.commonpath([site, baseline])
    if not os.path.exists(os.path.join(site, "index.json")):
        print("还没有索引，先构建一次…", flush=True)
        sys.path.insert(0, HERE)
        import build_library as bl
        bl.run(baseline, site, cfg)
    threading.Thread(target=watch, args=(baseline, site, cfg, not a.no_watch), daemon=True).start()
    url = f"http://127.0.0.1:{a.port}/" + "/".join(quote(x) for x in os.path.relpath(site, root).replace("\\", "/").split("/")) + "/"
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(root, site, baseline))
    print(f"例句库：{url}\n（只有本机能访问；关掉这个窗口或按 Ctrl+C 停止）")
    if not a.no_open: webbrowser.open(url)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass


if __name__ == "__main__":
    main()
