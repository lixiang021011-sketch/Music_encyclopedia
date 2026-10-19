"""例句库的不变量测试。只用标准库：

    python -m unittest discover -s tests -v          （在 tool 文件夹里运行）

1. 用合成的小导出（变速、换拍、弱起、最后一小节、重复歌、缺组件、坏 JSON）完整构建一遍，检查索引不变量；
2. 如果 ../site 里已经有构建结果，对真实索引再查一遍同样的不变量；
3. 本地服务器：Range 请求、路径穿越、未发布歌曲的音频、导出里的其他文件。
"""
import http.client, json, os, shutil, sys, tempfile, threading, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import build_library as bl                     # noqa: E402
import serve                                   # noqa: E402
from lib import publish                        # noqa: E402

ROOTS = {"C": 0, "G": 7, "A": 9, "F": 5}


# ---------- invariants (also run on the real site) ----------
def check_index(tc, idx, local=False):
    tc.assertTrue(idx.get("buildId"), "索引缺 buildId")
    tc.assertIn("T", idx.get("builtAt", ""), "builtAt 应为 ISO 8601")
    if not local:
        tc.assertNotIn("baseline", idx, "公开版索引不应带导出的绝对路径")
    ids = [s["id"] for s in idx["songs"]]
    tc.assertEqual(len(ids), len(set(ids)), "歌曲 ID 重复")
    for s in idx["songs"]:
        n = s["nbars"]
        tc.assertEqual([b["b"] for b in s["bars"]], list(range(1, n + 1)), f"{s['id']} 小节号不连续")
        if not local:
            tc.assertFalse(s.get("src"), f"{s['id']} 公开版不应带本机音频路径")
        for x in s["seq"]:
            tc.assertTrue(1 <= x["bar"] <= n, f"{s['id']} 和弦段落在第 {x['bar']} 小节（应在 1..{n}）")
        listed = {c["i"] for b in s["bars"] for c in b["ch"]}
        tc.assertEqual(listed, set(range(len(s["seq"]))), f"{s['id']} 有和弦段不在任何小节的 ch 里")
        for a, b in zip(s["bars"], s["bars"][1:]):
            tc.assertAlmostEqual(a["e"], b["t"], places=2, msg=f"{s['id']} 第 {a['b']} 小节结束 ≠ 下一小节开始")
        for b in s["bars"]:
            tc.assertGreater(b["e"], b["t"], f"{s['id']} 第 {b['b']} 小节长度不为正")
            tc.assertGreater(b["len"], 0, f"{s['id']} 第 {b['b']} 小节是 0 拍（重复小节线没去掉）")
        tier, src = s.get("tier"), idx["chordSource"]
        if src == "chart" and s.get("rootAgree") is not None:
            ra = s["rootAgree"]
            tc.assertEqual(tier, "高" if ra >= 0.70 else "中" if ra >= 0.55 else "低", f"{s['id']} 分级和根音一致率不符")
        if src == "template":
            kc = s.get("keyCoverage")
            tc.assertEqual(tier, None if kc is None or kc < 0.6 else ("高" if kc >= 0.8 else "中"), f"{s['id']} 分级和调覆盖不符")
        if tier not in ("高", "中"):
            tc.assertEqual(s["seq"], [], f"{s['id']} 分级 {tier} 却有可检索和弦")
        if s.get("bassSearchable"):
            tc.assertGreaterEqual(s["bassGridFit"] - s["bassGridChance"], 0.10 - 0.006, f"{s['id']} 贝斯开放检索但没过随机基线门槛")
            tc.assertLessEqual(s["irregularShare"], 0.25)
            tc.assertFalse(s["meterSuspect"])


# ---------- a tiny synthetic export ----------
def jl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")


def jw(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False)


def make_song(base, name, title, artist="Test", missing=(), bars=24, bpm=120.0, three_four_at=13, beat0=1.0, with_audio=True, dup_after=None):
    d = os.path.join(base, name); spb = 60 / bpm
    jw(os.path.join(d, "canonical", "song.json"), dict(schemaVersion="workbench.music.v2", songId=name, title=title, artist=artist,
                                                         originalFileName=name + ".flac", masterAudio="sources/audio/master.ogg",
                                                         missing=list(missing), health=dict(state="usable_with_reservations")))
    jw(os.path.join(d, "canonical", "parts.json"), [dict(partId=p, labels=[dict(type="role", value=p)]) for p in ("bass", "drums", "guitar")])
    # bar lines: 4/4 except one 3/4 bar; the last bar has a barSeconds longer than the rest (ritardando)
    meter, beat, irregular = [], 0.0, []
    for k in range(bars):
        num = 3 if k + 1 == three_four_at else 4
        meter.append(dict(beat=beat, numerator=num, denominator=4, beatsExact=float(num), barSeconds=num * spb * (1.5 if k == bars - 1 else 1)))
        beat += num
        if dup_after == k + 1:      # two bar lines 0.1 beat apart, listed with 0-based numbering like the later exports
            irregular.append(dict(bar=len(meter), atSeconds=beat0 + beat * spb, kind="duplicate-bar-line", beatsExact=0.1))
            meter.append(dict(beat=beat, numerator=0, denominator=4, beatsExact=0.1, barSeconds=0.1 * spb)); beat += 0.1
    jw(os.path.join(d, "canonical", "timing.json"), dict(tempoMap=[dict(beat=0, bpm=bpm)], meterMap=meter, irregularBars=irregular,
                                                         audioAlignment=dict(beat0AudioSeconds=beat0)))
    b2s = lambda b: beat0 + b * spb
    ev, H, T = [], [], []
    prog = ["C", "G", "A", "F"]
    for k, m in enumerate(x for x in meter if x["numerator"]):
        r = prog[k % 4]; q = "minor" if r == "A" else "major"; b0, b1 = m["beat"], m["beat"] + m["numerator"]
        H.append(dict(startSeconds=b2s(b0), endSeconds=b2s(b1), startBeat=b0, endBeat=b1, rootPitchClass=ROOTS[r], quality=q, symbol=r + ("m" if q == "minor" else "")))
        T.append(dict(startSeconds=b2s(b0), endSeconds=b2s(b1), rootPitchClass=ROOTS[r], quality=q, symbol=r + ("m" if q == "minor" else ""), bassPitchClass=None, coverage=0.95))
        for i in range(int(m["numerator"] * 2)):
            t = b0 + i / 2
            ev.append(dict(startSeconds=b2s(t), endSeconds=b2s(t + 0.4), startBeat=t, endBeat=t + 0.4, pitch=36 + ROOTS[r], velocity=90, partId="bass"))
        for i in range(int(m["numerator"])):
            t = b0 + i
            ev.append(dict(startSeconds=b2s(t), endSeconds=b2s(t), startBeat=t, endBeat=t, pitch=None, velocity=100, partId="drums", drum="kick" if i % 2 == 0 else "snare"))
            ev.append(dict(startSeconds=b2s(t + 0.5), endSeconds=b2s(t + 0.5), startBeat=t + 0.5, endBeat=t + 0.5, pitch=None, velocity=60, partId="drums", drum="closed hi-hat"))
    # a pickup before beat 0: chord and bass notes with negative beats (they are real audio time)
    H.insert(0, dict(startSeconds=b2s(-2), endSeconds=b2s(0), startBeat=-2, endBeat=0, rootPitchClass=7, quality="major", symbol="G"))
    T.insert(0, dict(startSeconds=b2s(-2), endSeconds=b2s(0), rootPitchClass=7, quality="major", symbol="G", bassPitchClass=None, coverage=0.95))
    # and a chord after the last bar line + its length (outro tail the bar grid does not cover)
    H.append(dict(startSeconds=b2s(beat + 1), endSeconds=b2s(beat + 3), startBeat=beat + 1, endBeat=beat + 3, rootPitchClass=0, quality="major", symbol="C"))
    for t in (-2, -1.5, -1, -0.5):
        ev.append(dict(startSeconds=b2s(t), endSeconds=b2s(t + 0.4), startBeat=t, endBeat=t + 0.4, pitch=43, velocity=90, partId="bass"))
    ev.sort(key=lambda e: e["startSeconds"])
    jl(os.path.join(d, "canonical", "events.jsonl"), ev)
    if "harmony" not in missing: jl(os.path.join(d, "annotations", "harmony.jsonl"), H)
    jl(os.path.join(d, "annotations", "harmony_template.jsonl"), T)
    if with_audio:
        os.makedirs(os.path.join(d, "sources", "audio"), exist_ok=True)
        with open(os.path.join(d, "sources", "audio", "master.ogg"), "wb") as f: f.write(bytes(range(256)) * 400)
    return d


class SyntheticBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="exlib_")
        cls.base = os.path.join(cls.tmp, "workbench_data_baseline"); cls.site = os.path.join(cls.tmp, "example_library", "site")
        make_song(cls.base, "alpha", "Alpha Song")
        make_song(cls.base, "alpha_copy", "Alpha Song")                          # duplicate of alpha
        make_song(cls.base, "nobass", "No Bass", missing=("bass",))              # fails the gate
        make_song(cls.base, "noaudio", "No Audio", with_audio=False)              # fails the gate
        make_song(cls.base, "beta", "Beta", bpm=96.0, three_four_at=5, dup_after=9)
        make_song(cls.base, "fresh", "Fresh Export")                             # in the export, not listed
        broken = make_song(cls.base, "broken", "Broken")
        with open(os.path.join(broken, "canonical", "timing.json"), "w") as f: f.write("{not json")
        cls.manifest = dict(include=["alpha", "alpha_copy", "nobass", "noaudio", "beta", "broken", "gone"], exclude={},
                            require=dict(schemaVersion="workbench.music.v2", allowHealth=["usable_with_reservations"],
                                         allowMissing=["harmony", "sections", "piano", "guitar"], needAudio=True))
        cls.report = bl.run(cls.base, cls.site, cfg={}, manifest=cls.manifest)
        cls.idx = {fn: json.load(open(os.path.join(cls.site, fn), encoding="utf-8")) for fn in ("index.json", "index_template.json")}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_only_published_songs(self):
        ids = {s["id"] for s in self.idx["index.json"]["songs"]}
        self.assertEqual(ids, {"wb_alpha", "wb_beta"})
        skipped = {s["song"]: s["reason"] for s in self.report["skipped"]}
        self.assertIn("重复", skipped["alpha_copy"]); self.assertIn("bass", skipped["nobass"])
        self.assertIn("音频", skipped["noaudio"]); self.assertIn("读取失败", skipped["broken"]); self.assertIn("没有", skipped["gone"])
        cand = {c["song"]: c for c in self.report["candidates"]}
        self.assertEqual(set(cand), {"fresh"}); self.assertTrue(cand["fresh"]["wouldPass"])

    def test_invariants(self):
        for idx in self.idx.values(): check_index(self, idx)

    def test_pickup_goes_to_bar_1_with_real_seconds(self):
        s = next(x for x in self.idx["index.json"]["songs"] if x["id"] == "wb_alpha")
        self.assertEqual(s["seq"][0]["bar"], 1)
        b1 = s["bars"][0]
        self.assertAlmostEqual(b1["pre"], 0.0, places=2)       # beat -2 at 120 BPM, beat 0 at 1.0 s
        self.assertAlmostEqual(b1["t"], 1.0, places=2)

    def test_meter_change_and_last_bar(self):
        s = next(x for x in self.idx["index.json"]["songs"] if x["id"] == "wb_beta")
        spb = 60 / 96
        self.assertEqual(s["bars"][4]["ts"], "3/4"); self.assertAlmostEqual(s["bars"][4]["e"] - s["bars"][4]["t"], 3 * spb, places=2)
        self.assertEqual(s["nbars"], 24, "重复小节线应去掉，且不能误删它前面那一小节")
        self.assertAlmostEqual(s["bars"][8]["e"] - s["bars"][8]["t"], 4 * spb + 0.1 * spb, places=2)
        last = s["bars"][-1]
        self.assertAlmostEqual(last["e"] - last["t"], 4 * spb * 1.5, places=2)   # from barSeconds, not from the BPM

    def test_build_metadata_and_cache(self):
        self.assertEqual(len(self.report["buildId"]), 12)
        snap = json.load(open(os.path.join(self.site, "_snapshot.json"), encoding="utf-8"))
        self.assertEqual(set(snap["indexed"]), {"alpha", "beta"}); self.assertIn("broken", snap["published"])
        st = bl.status(self.base, self.site, cfg={}, manifest=self.manifest)
        self.assertFalse(st["stale"], st["reasons"])
        # same inputs -> same build id
        again = bl.run(self.base, self.site, cfg={}, manifest=self.manifest)
        self.assertEqual(again["buildId"], self.report["buildId"])

    def test_status_sees_a_changed_song_but_not_new_folders(self):
        make_song(self.base, "another_new", "Another")
        st = bl.status(self.base, self.site, cfg={}, manifest=self.manifest)
        self.assertFalse(st["stale"]); self.assertEqual(st["candidates"], 2)
        p = os.path.join(self.base, "beta", "annotations", "harmony_template.jsonl")
        with open(p, encoding="utf-8") as f: rows = f.readlines()
        with open(p, "w", encoding="utf-8") as f: f.writelines(rows[:-1])
        st = bl.status(self.base, self.site, cfg={}, manifest=self.manifest)
        self.assertTrue(st["stale"]); self.assertEqual(st["staleSongs"], ["beta"])
        bl.run(self.base, self.site, cfg={}, manifest=self.manifest)
        self.assertFalse(bl.status(self.base, self.site, cfg={}, manifest=self.manifest)["stale"])

    def test_regression_gate(self):
        p = os.path.join(self.base, "alpha", "canonical", "events.jsonl")
        with open(p, encoding="utf-8") as f: rows = f.readlines()
        bass = [i for i, r in enumerate(rows) if '"bass"' in r]
        keep = [r for i, r in enumerate(rows) if i not in set(bass[::2])]      # swallow every other bass onset
        with open(p, "w", encoding="utf-8") as f: f.writelines(keep)
        before = next(x for x in json.load(open(os.path.join(self.site, "index.json"), encoding="utf-8"))["songs"] if x["id"] == "wb_alpha")
        rep = bl.run(self.base, self.site, cfg={}, manifest=self.manifest)
        self.assertEqual([h["song"] for h in rep["heldBack"]], ["alpha"])
        now = next(x for x in json.load(open(os.path.join(self.site, "index.json"), encoding="utf-8"))["songs"] if x["id"] == "wb_alpha")
        self.assertEqual(now["bassOnsets"], before["bassOnsets"]); self.assertTrue(now["heldBack"])
        rep = bl.run(self.base, self.site, cfg={}, manifest=self.manifest, accept_drops=True)
        self.assertEqual(rep["heldBack"], [])
        now = next(x for x in json.load(open(os.path.join(self.site, "index.json"), encoding="utf-8"))["songs"] if x["id"] == "wb_alpha")
        self.assertLess(now["bassOnsets"], before["bassOnsets"])
        with open(p, "w", encoding="utf-8") as f: f.writelines(rows)
        bl.run(self.base, self.site, cfg={}, manifest=self.manifest, accept_drops=True)

    def test_lock(self):
        from lib.lock import BuildLock, Busy
        with BuildLock(os.path.join(self.site, ".build.lock")):
            with self.assertRaises(Busy):
                bl.run(self.base, self.site, cfg={}, manifest=self.manifest, wait=0)


class RealSite(unittest.TestCase):
    """invariants on the library as it is built now (skipped when there is no build yet)"""
    def test_real_indexes(self):
        site = bl.paths(bl.load_cfg().get("baseline", "."), bl.load_cfg().get("out"))[1]
        files = [os.path.join(site, f) for f in ("index.json", "index_template.json")]
        if not all(os.path.exists(f) for f in files): self.skipTest("还没有构建")
        for f in files:
            idx = json.load(open(f, encoding="utf-8"))
            if "buildId" not in idx: self.skipTest("旧版索引，重建后再测")
            check_index(self, idx, local=idx.get("local", False))

    def test_manifest_is_valid(self):
        m = publish.load(os.path.join(HERE, "publish.json"))
        self.assertEqual(len(m["include"]), len(set(m["include"])), "include 里有重复")
        self.assertFalse(set(m["include"]) & set(m["exclude"]), "同一首歌同时在 include 和 exclude 里")


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="exlib_srv_")
        cls.base = os.path.join(cls.tmp, "workbench_data_baseline"); cls.site = os.path.join(cls.tmp, "example_library", "site")
        make_song(cls.base, "alpha", "Alpha"); make_song(cls.base, "hidden", "Hidden")
        os.makedirs(cls.site)
        with open(os.path.join(cls.site, "index.html"), "w") as f: f.write("<!doctype html>ok")
        with open(os.path.join(cls.site, "_snapshot.json"), "w") as f: json.dump(dict(published={"alpha": "x"}, indexed=["alpha"]), f)
        os.makedirs(os.path.join(cls.site, ".staging"))
        with open(os.path.join(cls.site, ".staging", "x.json"), "w") as f: f.write("{}")
        root = os.path.commonpath([cls.site, cls.base])
        H = serve.make_handler(serve.resolve(root), serve.resolve(cls.site), serve.resolve(cls.base))
        cls.srv = serve.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path, headers=headers or {}); r = c.getresponse(); body = r.read(); c.close()
        return r.status, dict(r.getheaders()), body

    def test_page(self):
        self.assertEqual(self.get("/example_library/site/")[0], 200)

    def test_range(self):
        st, h, body = self.get("/workbench_data_baseline/alpha/sources/audio/master.ogg", {"Range": "bytes=100-199"})
        self.assertEqual(st, 206); self.assertEqual(len(body), 100); self.assertEqual(body[0], 100)
        self.assertTrue(h["Content-Range"].startswith("bytes 100-199/"))
        self.assertEqual(self.get("/workbench_data_baseline/alpha/sources/audio/master.ogg", {"Range": "bytes=999999999-"})[0], 416)

    def test_blocked(self):
        for p in ("/workbench_data_baseline/hidden/sources/audio/master.ogg",       # not published
                  "/workbench_data_baseline/alpha/canonical/song.json",            # export data
                  "/example_library/site/../../workbench_data_baseline/alpha/canonical/events.jsonl",
                  "/example_library/site/%2e%2e/%2e%2e/workbench_data_baseline/alpha/canonical/song.json",
                  "/example_library/site/.staging/x.json"):
            self.assertEqual(self.get(p)[0], 404, p)


if __name__ == "__main__":
    unittest.main()
