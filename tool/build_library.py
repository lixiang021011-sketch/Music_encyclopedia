"""原曲转写例句库：从 Workbench 导出重建索引。

只收 publish.json 里 include 列出的歌。导出里其他的歌只进报告（待审核），不进例句库。

读  workbench_data_baseline/<歌>/canonical + annotations + features（只读）
写  <out>/index.json            和弦来源 A：上游和弦图
    <out>/index_template.json   和弦来源 B：模板读法（规范 v0）
    <out>/index.html            页面
    <out>/build_report.json     这一版的版本号、每首歌的分级与门槛、跳过的歌、待审核的歌、和上一版的差异
    <out>/_snapshot.json        这一版用到的输入指纹，serve.py 用它判断索引是否过时

用法:
    python build_library.py                 # 公开版：索引里不带本机绝对路径
    python build_library.py --local         # 本地调试版：额外写入音频和导出的绝对路径
    python build_library.py --no-regrid     # 跳过鼓点重对位
    python build_library.py --songs eine    # 这几首的重对位不用缓存
    python build_library.py --candidates    # 只列出导出里待审核的歌，不构建
只用 Python 标准库，3.8 以上都能跑。
"""
import argparse, datetime, hashlib, json, os, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib import extract, regrid, audio_index, publish   # noqa: E402
from lib.lock import BuildLock, Busy                     # noqa: E402

CACHE_VERSION = 2
DROP = 0.30           # regression gate: hold a song back when bass onsets or root agreement fall by this share
NOTES = {
    "chart": "上游和弦图 chord_chart_v2（半小节大三/小三，解码时参考了贝斯）",
    "template": "模板读法，按《纵向和声抓取规范 v0》：不读贝斯，带七和弦与转位；按大小三和弦的调覆盖定调（< 60% 不标级数）；级数只看大小不看延长音，挂留/强力和弦只标根音级数（浅色）。校准集真值音符上限根音 0.93、根音+大小 0.905，真实录音未验证",
}
CODE_FILES = ["build_library.py", "lib/extract.py", "lib/audio_index.py", "lib/harmony.py", "lib/regrid.py", "lib/publish.py", "page/index.html"]


# ---------- small helpers ----------
def load_json(p, default):
    try:
        with open(p, encoding="utf-8") as f: return json.load(f)
    except (OSError, ValueError):
        return default


def dump_json(p, obj, pretty=False):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, **({"indent": 1} if pretty else {"separators": (",", ":")}))


def save_json(p, obj, pretty=False):
    tmp = p + ".tmp"; dump_json(tmp, obj, pretty); os.replace(tmp, p)


def sha1_files(paths):
    h = hashlib.sha1()
    for p in paths:
        h.update(os.path.relpath(p, HERE).replace("\\", "/").encode() + b"\0")
        with open(p, "rb") as f: h.update(f.read())
    return h.hexdigest()


def sha1_obj(o):
    return hashlib.sha1(json.dumps(o, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def load_cfg():
    """config.json (in the repo) overlaid with config.local.json (this machine's paths, not in the repo)"""
    cfg = load_json(os.path.join(HERE, "config.json"), {})
    cfg.update(load_json(os.path.join(HERE, "config.local.json"), {}))
    return cfg


def load_manifest():
    return publish.load(os.path.join(HERE, "publish.json"))


def paths(baseline, out):
    base = os.path.abspath(baseline if os.path.isabs(baseline) else os.path.join(HERE, baseline))
    return base, os.path.abspath(out if out and os.path.isabs(out) else os.path.join(HERE, out or "../site"))


def config_hash(cfg, manifest):
    return sha1_obj(dict(config={k: v for k, v in cfg.items() if not k.startswith("_") and k != "port"}, publish=manifest))


def code_hash():
    return sha1_files([os.path.join(HERE, f) for f in CODE_FILES if os.path.exists(os.path.join(HERE, f))])


# ---------- staleness (also used by serve.py) ----------
def status(base, out, cfg=None, manifest=None):
    """is the built index still what the export + config + code would give now?"""
    cfg = load_cfg() if cfg is None else cfg
    manifest = load_manifest() if manifest is None else manifest
    snap = load_json(os.path.join(out, "_snapshot.json"), None)
    dirs = list(extract.song_dirs(base))
    published, sel = publish.select(dirs, manifest)
    st = dict(checkedAt=now_iso(), buildId=snap and snap.get("buildId"), candidates=len(sel["candidates"]),
              skipped=len(sel["skipped"]), staleSongs=[], reasons=[])
    now = {n: extract.fingerprint(d) for n, d in published}
    st["inputKey"] = sha1_obj([now, config_hash(cfg, manifest), code_hash()])     # equal on two polls = export finished writing
    if not snap:
        st.update(stale=True, reasons=["还没有构建过"]); return st
    old = snap.get("published", {})
    st["staleSongs"] = sorted(n for n in set(old) | set(now) if old.get(n) != now.get(n))
    if st["staleSongs"]: st["reasons"].append("已发布的歌在导出里有变化，或发布名单变了")
    if snap.get("configHash") != config_hash(cfg, manifest): st["reasons"].append("config.json 或 publish.json 改过")
    if snap.get("codeHash") != code_hash(): st["reasons"].append("构建程序改过")
    st["stale"] = bool(st["reasons"])
    return st


# ---------- build ----------
def run(base, out, cfg=None, no_regrid=False, songs="", local=False, wait=120, manifest=None, accept_drops=False):
    cfg = load_cfg() if cfg is None else cfg
    manifest = load_manifest() if manifest is None else manifest
    os.makedirs(out, exist_ok=True)
    with BuildLock(os.path.join(out, ".build.lock"), wait=wait):
        return _run(base, out, cfg, manifest, no_regrid, songs, local, accept_drops)


def _run(base, out, cfg, manifest, no_regrid, songs, local, accept_drops=False):
    t0 = time.time()
    dirs = list(extract.song_dirs(base))
    published, sel = publish.select(dirs, manifest)
    for s in sel["skipped"]: print(f"  跳过 {s['song']}：{s['reason']}")

    # 1. read the published songs (read-only); fingerprints are taken first, so a change during the build shows up as stale
    finger = {n: extract.fingerprint(d) for n, d in published}
    data, chash = {}, {}
    for name, d in published:
        try:
            chash[name] = extract.content_hash(d)
            data[name] = extract.read_song(d)
        except Exception as e:                       # one broken song must not take the library down
            sel["skipped"].append(dict(song=name, reason=f"读取失败（{type(e).__name__}: {e}）"))
            chash.pop(name, None)            # its fingerprint stays in the snapshot: fixing the file makes the build stale
            print(f"  跳过 {name}：读取失败（{type(e).__name__}: {e}）")
    if not data: raise SystemExit("没有可以发布的歌：检查 publish.json 的 include 和 build_report.json 的 skipped")
    print(f"读入 {len(data)} 首；导出里另有 {len(sel['candidates'])} 首待审核（{time.time() - t0:.1f} 秒）")

    cfg_h, code_h = config_hash(cfg, manifest), code_hash()
    src_h = sha1_obj(sorted(chash.items()))
    build_id = hashlib.sha1(f"{src_h}|{cfg_h}|{code_h}|{int(no_regrid)}|{int(local)}".encode()).hexdigest()[:12]
    built_at = now_iso()

    # 2. drum-anchored re-timing, cached by input content + the re-timing code itself
    regrid_h = sha1_files([os.path.join(HERE, "lib", "regrid.py")])
    cache_p = os.path.join(out, "_regrid_cache.json")
    cache = load_json(cache_p, {})
    if cache.get("v") != CACHE_VERSION or cache.get("code") != regrid_h: cache = dict(v=CACHE_VERSION, code=regrid_h, songs={})
    force = {x.strip() for x in songs.split(",") if x.strip()}
    regrid_log = {}
    if not no_regrid:
        for name, s in data.items():
            c = cache["songs"].get(name)
            if c and c.get("hash") == chash[name] and name not in force:
                r = c["r"]
            else:
                t1 = time.time()
                try:
                    r = regrid.retime(s)
                except Exception as e:
                    r = dict(adopted=False, reason=f"重对位出错（{type(e).__name__}: {e}），保留上游和弦图")
                cache["songs"][name] = dict(hash=chash[name], r=r)
                print(f"  重对位 {name}: {r['reason']}（{time.time() - t1:.1f} 秒）")
            if r.get("adopted"):
                s["harmony"] = r["harmony"]; s["chartRetimed"] = True
            regrid_log[name] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k != "harmony"}
        cache["songs"] = {n: v for n, v in cache["songs"].items() if n in data}

    # 3. everything is written into a staging folder first, then moved into place file by file
    stage = os.path.join(out, ".staging")
    shutil.rmtree(stage, ignore_errors=True); os.makedirs(stage, exist_ok=True)
    meta = dict(generatedFrom="workbench_data_baseline (workbench.music.v2)", source="audio", buildId=build_id, builtAt=built_at,
                sourceContentHash=src_h, configHash=cfg_h, codeHash=code_h, regrid=not no_regrid, local=local)
    if local: meta["baseline"] = base
    prev, new = {}, {}
    for src, fn in (("chart", "index.json"), ("template", "index_template.json")):
        prev[src] = {x["id"]: x for x in load_json(os.path.join(out, fn), {}).get("songs", [])}
        built, _ = audio_index.build(data, src, baseline=base, site=out, swing=cfg.get("swing"),
                                     suspect=cfg.get("suspectMeter"), local=local)
        new[src] = {x["id"]: x for x in built}
    # regression gate: a song whose bass onsets or bass-root agreement fell by >= DROP against the last build is
    # not published this time; the previous version of that song stays in both indexes until someone accepts it
    held = []
    if not accept_drops:
        for sid, n in new["chart"].items():
            o = prev["chart"].get(sid)
            if not o: continue
            why = []
            if o.get("bassOnsets") and n.get("bassOnsets") is not None and n["bassOnsets"] < (1 - DROP) * o["bassOnsets"]:
                why.append(f"贝斯起音 {o['bassOnsets']} → {n['bassOnsets']}")
            if o.get("rootAgree") and n.get("rootAgree") is not None and n["rootAgree"] < (1 - DROP) * o["rootAgree"]:
                why.append(f"根音一致率 {o['rootAgree']} → {n['rootAgree']}")
            if why and all(sid in prev[src] for src in new):
                held.append(dict(song=sid[3:], reason="；".join(why)))
                for src in new: new[src][sid] = dict(prev[src][sid], heldBack=True)
    for src, fn in (("chart", "index.json"), ("template", "index_template.json")):
        dump_json(os.path.join(stage, fn), dict(meta, chordSource=src, chordSourceNote=NOTES[src], held=[h["song"] for h in held],
                                               songs=list(new[src].values())))
    page = os.path.join(HERE, "page", "index.html")
    shutil.copyfile(page, os.path.join(stage, "index.html"))

    # 4. report
    rows, changes = [], []
    for name in sorted(data):
        sid = "wb_" + name
        ch, tp = new["chart"].get(sid, {}), new["template"].get(sid, {})
        rows.append(dict(song=name, songId=data[name]["song"].get("songId"), title=ch.get("title"), artist=ch.get("artist"),
                         bars=ch.get("nbars"), missing=data[name]["song"].get("missing") or [], contentHash=chash[name][:12],
                         chartTier=ch.get("tier"), rootAgree=ch.get("rootAgree"), chartChords=len(ch.get("seq", [])),
                         templateTier=tp.get("tier"), keyCoverage=tp.get("keyCoverage"), templateKey=tp.get("globalKey"),
                         bassSearchable=ch.get("bassSearchable"), bassGrid=ch.get("bassGrid"),
                         bassFitMinusChance=None if ch.get("bassGridFit") is None else round(ch["bassGridFit"] - ch["bassGridChance"], 2),
                         audio=bool(ch.get("srcRel")), regrid=regrid_log.get(name)))
        for src, label in (("chart", "和弦图"), ("template", "模板")):
            o, n = prev[src].get(sid), new[src].get(sid)
            if o is None and prev[src]: changes.append(f"{name}：新加入（{label}）")
            elif o is not None and n is not None:
                if o.get("tier") != n.get("tier"): changes.append(f"{name}：{label}分级 {o.get('tier') or '无'} → {n.get('tier') or '无'}")
                if len(o.get("seq", [])) != len(n.get("seq", [])): changes.append(f"{name}：{label}可检索和弦 {len(o.get('seq', []))} → {len(n.get('seq', []))}")
            if src == "chart" and o is not None and n is not None and o.get("bassSearchable") != n.get("bassSearchable"):
                changes.append(f"{name}：贝斯律动检索 {'开' if o.get('bassSearchable') else '关'} → {'开' if n.get('bassSearchable') else '关'}")
    for sid in prev["chart"]:
        if sid not in new["chart"]: changes.append(f"{sid[3:]}：不再发布，移出例句库")
    cnt = lambda f: sum(1 for r in rows if f(r))
    summary = dict(songs=len(rows), chartSearchable=cnt(lambda r: r["chartTier"] in ("高", "中")),
                   templateSearchable=cnt(lambda r: r["templateTier"] in ("高", "中")), bassSearchable=cnt(lambda r: r["bassSearchable"]),
                   regridAdopted=cnt(lambda r: (r["regrid"] or {}).get("adopted")), withAudio=cnt(lambda r: r["audio"]),
                   candidates=len(sel["candidates"]), skipped=len(sel["skipped"]))
    report = dict(buildId=build_id, builtAt=built_at, seconds=None, sourceContentHash=src_h, configHash=cfg_h, codeHash=code_h,
                  regrid=not no_regrid, local=local, summary=summary, songs=rows, skipped=sel["skipped"], excluded=sel["excluded"],
                  candidates=sel["candidates"], heldBack=held, changesSinceLastBuild=changes,
                  note="changesSinceLastBuild 只比较这一版和上一版索引；索引是否跟得上当前导出，看 serve.py 写的 _status.json 或页面顶部提示")
    snap = dict(buildId=build_id, builtAt=built_at, configHash=cfg_h, codeHash=code_h, published=finger, indexed=sorted(data))

    # 5. move into place: indexes, page, report, then the snapshot last (a crash in between leaves the build marked stale)
    report["seconds"] = round(time.time() - t0, 1)
    dump_json(os.path.join(stage, "build_report.json"), report, pretty=True)
    for fn in ("index.json", "index_template.json", "index.html", "build_report.json"):
        os.replace(os.path.join(stage, fn), os.path.join(out, fn))
    if not no_regrid: save_json(cache_p, cache)
    save_json(os.path.join(out, "_snapshot.json"), snap, pretty=True)
    shutil.rmtree(stage, ignore_errors=True)
    for old in ("_snapshot.txt",):
        try: os.remove(os.path.join(out, old))
        except OSError: pass

    print(f"\n完成：版本 {build_id}，{len(rows)} 首，{report['seconds']} 秒 → {out}")
    print(f"  和弦图可检索 {summary['chartSearchable']} 首；模板可检索 {summary['templateSearchable']} 首；"
          f"贝斯律动可检索 {summary['bassSearchable']} 首；重对位采用 {summary['regridAdopted']} 首；有原曲音频 {summary['withAudio']} 首")
    for h in held: print(f"  ⚠ 暂不更新 {h['song']}：{h['reason']}（下降 ≥ {int(DROP * 100)}%，沿用上一版；确认无误后用 --accept-drops 重建）")
    if sel["skipped"]: print(f"  发布名单里有 {len(sel['skipped'])} 首没通过门槛，见 build_report.json → skipped")
    if sel["candidates"]:
        ok = sum(1 for c in sel["candidates"] if c["wouldPass"])
        print(f"  导出里另有 {len(sel['candidates'])} 首没在发布名单里（其中 {ok} 首能通过门槛）。要发布，把歌名加进 publish.json 的 include")
    if changes:
        print("  和上一版索引相比：")
        for c in changes: print("   ·", c)
    elif prev["chart"]:
        print("  和上一版索引相比：没有变化")
    return report


def print_candidates(base):
    _, sel = publish.select(list(extract.song_dirs(base)), load_manifest())
    print(f"待审核 {len(sel['candidates'])} 首（不在 publish.json 的 include 里）：")
    for c in sel["candidates"]:
        flag = "可发布" if c["wouldPass"] else "；".join(c["problems"] + ([f"和已发布的 {c['duplicateOf']} 重复"] if c["duplicateOf"] else [])) or "—"
        same = f"  同名：{'、'.join(c['sameTitleAs'])}" if c.get("sameTitleAs") else ""
        print(f"  {c['song']:<14} {c['title'] or ''} / {c['artist'] or ''}  [{flag}]{same}")


def main():
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    cfg = load_cfg()
    ap = argparse.ArgumentParser(description="从 workbench_data_baseline 重建原曲转写例句库（只收 publish.json 里的歌）")
    ap.add_argument("--baseline", default=cfg.get("baseline"))
    ap.add_argument("--out", default=cfg.get("out"))
    ap.add_argument("--no-regrid", action="store_true", help="不做鼓点重建小节线与和弦图重对位")
    ap.add_argument("--songs", default="", help="逗号分隔；这些歌的重对位不用缓存")
    ap.add_argument("--local", action="store_true", help="本地调试版：索引里写入本机绝对路径")
    ap.add_argument("--candidates", action="store_true", help="只列出导出里待审核的歌")
    ap.add_argument("--accept-drops", action="store_true", help="接受指标大幅下降的歌（默认沿用上一版）")
    a = ap.parse_args()
    if not a.baseline: sys.exit("缺少 --baseline（或在 config.json 里写 baseline）")
    base, out = paths(a.baseline, a.out)
    if a.candidates: print_candidates(base); return
    try:
        run(base, out, cfg, a.no_regrid, a.songs, a.local, accept_drops=a.accept_drops)
    except Busy as e:
        sys.exit(str(e) + "，等它结束再试")


if __name__ == "__main__":
    main()
