"""Add the Workbench audio songs (workbench.music.v1 baseline) to the library index.

Coordinates: audio seconds are primary. Bars/beats come from the baseline's per-bar tempo map and
meter map and are a view only. Everything here is machine transcription of separated stems:
  - harmony: half-bar major/minor triads (chord_chart_v2), searchable only in songs whose
    bass-root agreement (agreed definition) is >= 0.55
  - bass groove: basic_pitch bass, onsets snapped to the nearest 8th (16th positions are not trusted)
  - no melody (no vocal pitch), only a global key estimate, no modulations
  - advanced harmony: candidates with evidence only
Called by build_library.py; not run on its own.
"""
import json, os, sys, bisect, collections, math, copy
from urllib.parse import quote
from .harmony import key_name, chord_name, roman, is_diatonic, match_named, KK_MAJ, KK_MIN, base_numeral, CHORD_TYPES, COLOR_TAG, bass_degree, fam
QMAP = {"major": "", "minor": "m"}
# --- spec-chord-vertical-v0 §5: key by chord coverage of the natural scale, standard roman numerals ---
SPEC_MAJOR = {1: (0, "maj"), 2: (2, "min"), 3: (4, "min"), 4: (5, "maj"), 5: (7, "maj"), 6: (9, "min"), 7: (11, "dim")}
SPEC_MINOR = {1: (0, "min"), 2: (2, "dim"), 3: (3, "maj"), 4: (5, "min"), 5: (7, "min"), 6: (8, "maj"), 7: (10, "maj")}
SPEC_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII"}
MAJ_T = {"", "7", "maj7", "6", "add9", "maj9", "9", "7b9"}
MIN_T = {"m", "m7", "m6", "madd9", "m9", "mM7"}
DIM_T = {"dim", "dim7", "m7b5"}
def majmin(typ): return "maj" if typ in MAJ_T else "min" if typ in MIN_T else None
def spec_key(rows):
    """rows: (root, type, seconds); returns (tonic, mode, coverage) by the spec's estimate_key"""
    best = None; chords = [(r, majmin(t), sec) for r, t, sec in rows if majmin(t)]
    for mode, table in (("major", SPEC_MAJOR), ("minor", SPEC_MINOR)):
        inv = {v: k for k, v in table.items()}
        for tonic in range(12):
            tot = hit = 0.0; degs = []
            for r, q, sec in chords:
                d = inv.get(((r - tonic) % 12, q)); degs.append(d); tot += sec
                if d is not None: hit += sec
            if not tot: continue
            sc = (hit / tot, 1 if degs and degs[0] == 1 else 0)
            if best is None or sc > best[0]: best = (sc, tonic, mode, hit / tot)
    return None if best is None else (best[1], best[2], best[3])
def spec_roman(root, typ, tonic, mode):
    """(numeral, kind): kind 'full' = degree with quality; 'root' = root-only degree (sus/5/aug...); 'out' = not in the scale"""
    table = SPEC_MAJOR if mode == "major" else SPEC_MINOR
    off = (root - tonic) % 12; q = majmin(typ) or ("dim" if typ in DIM_T else None)
    for d, (semi, exp) in table.items():
        if semi == off and q == exp:
            t = SPEC_ROMAN[d]
            return (t.lower() + "°" if exp == "dim" else t.lower() if exp == "min" else t), "full"
    for d, (semi, _) in table.items():
        if semi == off:
            return SPEC_ROMAN[d] + ("+" if typ == "aug" else ""), "root"
    return None, "out"


TPQ = 480
# songs whose kick or snare is swung (Workbench drum annotation, 2026-10-11): the bass is judged against a swing grid
# both sets are read from config.json at build time (these are the defaults)
SWING = {"atom", "chichunhua", "eine", "happyend", "hitchcock", "kiechau", "natsuyuki"}
SUSPECT_METER = {"flyway", "flywayleo", "haiiro", "iloveyou", "hitchcock", "sacra"}   # 2:3 / 4:3 / 2:1 tempo readings
PROG = {"bass": 33, "guitar": 25, "piano": 0, "drums": 0}
ROLE = {"bass": "bass", "guitar": "other", "piano": "other", "drums": "drums"}
NAME = {"bass": "贝斯（basic_pitch 转写）", "guitar": "吉他（basic_pitch 转写）", "piano": "钢琴（ByteDance 转写）", "drums": "鼓（ADTOF 转写）"}


def corr(a, b):
    ma, mb = sum(a) / 12, sum(b) / 12
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / den if den else 0


def kk(prof):
    best = None
    for t in range(12):
        for mode, P in (("major", KK_MAJ), ("minor", KK_MIN)):
            s = corr(prof, [P[(i - t) % 12] for i in range(12)])
            if best is None or s > best[0]: best = (s, t, mode)
    return best


def timeline(tim):
    tm = sorted(tim["tempoMap"]); beats0 = [b for b, _ in tm]
    secs = [tim["beat0"]]
    for i in range(1, len(tm)):
        secs.append(secs[-1] + (tm[i][0] - tm[i - 1][0]) * 60 / tm[i - 1][1])

    def b2s(beat):
        i = max(0, bisect.bisect_right(beats0, beat) - 1)
        return secs[i] + (beat - tm[i][0]) * 60 / tm[i][1]

    def s2b(sec):
        i = max(0, bisect.bisect_right(secs, sec) - 1)
        return tm[i][0] + (sec - secs[i]) * tm[i][1] / 60
    return b2s, s2b


def bar_list(tim, last_beat):
    mm = sorted(tim["meterMap"]); bars = []; beat = 0.0; k = 0
    while beat <= last_beat + 1e-6:
        while k + 1 < len(mm) and mm[k + 1][0] <= beat + 1e-6: k += 1
        num, den = mm[k][1], mm[k][2]
        L = num * 4 / den
        bars.append(dict(bar=len(bars) + 1, startBeat=beat, lengthBeats=L, numerator=num, denominator=den))
        beat += L
    return bars


def build(data, source="chart", baseline="", site="", swing=None, suspect=None, local=False):
    global SWING, SUSPECT_METER
    if swing is not None: SWING = set(swing)
    if suspect is not None: SUSPECT_METER = set(suspect)
    data = copy.deepcopy(data)          # the builder rewrites d["harmony"]; keep the caller's copy intact
    songs, report = [], []
    for name, d in sorted(data.items()):
        tim = d["timing"]; b2s, _ = timeline(tim)
        # v2 contract: one meterMap entry per bar line, beats are cumulative actual beats. For the bar view we use a
        # musical-beat axis: each bar gets its nominal length (numerator, or the rounded actual count for bars flagged
        # bar-length-differs), positions inside a bar are linear in audio seconds. Duplicate bar lines are dropped.
        irr = {x["bar"]: x["kind"] for x in tim.get("irregularBars", [])}
        lines = []
        for k, m in enumerate(sorted(tim["meterMap"])):
            barno = k + 1
            if irr.get(barno) == "duplicate-bar-line": continue
            nominal = m[1] * 4 / m[2]
            if irr.get(barno) == "bar-length-differs" and m[3]: nominal = max(1, round(m[3]))
            if nominal <= 0: continue                     # a bar line with no length is a duplicate, whatever the flags say
            lines.append(dict(sec=b2s(m[0]), L=nominal, num=m[1], den=m[2], irregular=barno in irr, barSec=m[4]))
        bars, M = [], 0.0
        for i, ln in enumerate(lines):
            end = lines[i + 1]["sec"] if i + 1 < len(lines) else ln["sec"] + (ln.get("barSec") or (ln["sec"] - lines[i - 1]["sec"] if i > 0 else 2.0))
            bars.append(dict(bar=i + 1, startBeat=M, lengthBeats=ln["L"], numerator=ln["num"], denominator=ln["den"],
                             startSec=ln["sec"], endSec=end, irregular=ln["irregular"]))
            M += ln["L"]
        bsecs = [b["startSec"] for b in bars]

        def s2b(t):
            i = min(max(0, bisect.bisect_right(bsecs, t) - 1), len(bars) - 1)
            b = bars[i]; span = (b["endSec"] - b["startSec"]) or 1e-6
            return b["startBeat"] + (t - b["startSec"]) / span * b["lengthBeats"]
        ev = [[e[0], e[1], s2b(e[0]), s2b(e[1])] + e[4:] for e in d["events"]]
        # harmony rows -> [startSec, endSec, startBeat, endBeat, root, type, symbol, bassPc, coverage]
        if source == "template":
            raw = [[h[0], h[1], h[2], QMAP.get(h[3], h[3]), h[4], h[5], h[6]] for h in d.get("template", [])]
        else:
            raw = [[h[0], h[1], h[4], QMAP.get(h[5], h[5]), h[6], None, None] for h in d["harmony"]]
        d["harmony"] = [[r[0], r[1], s2b(r[0]), s2b(r[1]), r[2], r[3], r[4], r[5], r[6]] for r in raw if r[3] in CHORD_TYPES]
        bstarts = [b["startBeat"] for b in bars]
        for b in bars:
            b["startTick"] = round(b["startBeat"] * TPQ); b["lengthTicks"] = round(b["lengthBeats"] * TPQ)
        bar_of = lambda beat: bars[max(0, bisect.bisect_right(bstarts, beat) - 1)]["bar"]   # beat < 0 (before the first downbeat) -> bar 1
        caution = {b["bar"] for b in bars if b["irregular"]}
        for sb, L, t in d["windows"]:
            if t != "eligible":
                for k in range(L): caution.add(sb + 1 + k)
        # ---- bass line: lowest note per 30 ms onset group (octave leak / overtones sit above) ----
        bass_all = sorted([e for e in ev if e[6] == "bass" and e[4] is not None], key=lambda e: (e[0], e[4]))
        line = []
        for e in bass_all:
            if line and e[0] - line[-1][0] < 0.03: continue
            line.append(e)
        # grid fit in milliseconds, compared with a chance baseline (same onsets shifted by fractions of a beat):
        # +-25 ms around straight 16th positions, skipping bars whose tempo entry is broken (< 30 or > 400 BPM)
        def fit(times, shift=0.0):
            h = n = 0
            for t in times:
                b = s2b(t)
                spb = s2b(t + 0.001) - b; spb = 0.001 / spb if spb > 0 else 0
                if not 0.15 <= spb <= 2.0 or b < 0: continue
                b += shift; f = b - math.floor(b)
                n += 1; h += min(abs(f - p) for p in nonlocal_pts[0]) * spb <= 0.025
            return h / n if n else 0
        onsets = [e[0] for e in line]
        STRAIGHT, SWUNG = (0, .25, .5, .75, 1), (0, 2 / 3, 1)

        def lift(pts):
            nonlocal_pts[0] = pts
            f = fit(onsets); c = sum(fit(onsets, k / 8 + 1 / 16) for k in range(8)) / 8   # baseline on the same grid
            return f, c
        nonlocal_pts = [STRAIGHT]
        grid_pts = STRAIGHT
        grid_fit, grid_chance = lift(STRAIGHT); grid_used = "直拍"
        if name in SWING:          # the drums swing here: try the swing grid too, keep whichever beats its own baseline by more
            f2, c2 = lift(SWUNG)
            if f2 - c2 > grid_fit - grid_chance: grid_fit, grid_chance, grid_used = f2, c2, "摇摆"
        bass_ok = grid_fit - grid_chance >= 0.10
        irr_share = sum(1 for b in bars if b["irregular"]) / max(len(bars), 1)
        bass_block = None if bass_ok else "贝斯起音贴拍不比随机好多少，这首不做律动检索"
        if bass_ok and irr_share > 0.25:
            bass_ok = False; bass_block = f"不规则小节占 {round(irr_share * 100)}%，这首不做律动检索"
        # ---- harmony + agreed root-agreement metric (all bass onsets, micro) ----
        H = d["harmony"]
        ts = [e[0] for e in bass_all]; hits = n = 0; seg_ev = []
        for h in H:
            j = bisect.bisect_left(ts, h[0]); sh = sn = 0
            for e in bass_all[j:]:
                if e[0] >= h[1]: break
                sn += 1; sh += e[4] % 12 == h[4]
            hits += sh; n += sn; seg_ev.append((sh, sn))
        agree = hits / n if n else None
        tier = None if agree is None else ("高" if agree >= 0.70 else "中" if agree >= 0.55 else "低")
        # ---- key ----
        prof = [0.0] * 12
        if H:
            for h in H:
                w = h[1] - h[0]
                for iv in CHORD_TYPES[h[5]]: prof[(h[4] + iv) % 12] += w
            keysrc = "由和弦估计"
        else:
            for e in ev:
                if e[6] in ("guitar", "piano", "bass") and e[4] is not None: prof[e[4] % 12] += max(e[1] - e[0], 0.05)
            keysrc = "由转写音符估计（无和弦图）"
        ks, tonic, mode = kk(prof)
        if H:   # chord charts: pick the key whose diatonic triads cover the most time, then major vs relative minor by tonic-triad time
            dur = collections.Counter()
            for h in H: dur[(h[4], h[5])] += h[1] - h[0]
            tot = sum(dur.values()) or 1
            def fit(t):
                return sum(w for (r, q), w in dur.items() if {(r + i) % 12 for i in CHORD_TYPES[q]} <= {(t + i) % 12 for i in (0, 2, 4, 5, 7, 9, 11)}) / tot
            best_t = max(range(12), key=lambda t: (round(fit(t), 3), corr(prof, [KK_MAJ[(i - t) % 12] for i in range(12)])))
            maj_w = sum(w for (r, q), w in dur.items() if r == best_t and fam(q) == "M")
            min_w = sum(w for (r, q), w in dur.items() if r == (best_t + 9) % 12 and fam(q) == "m")
            tonic, mode = (best_t, "major") if maj_w >= min_w else ((best_t + 9) % 12, "minor")
            ks = fit(best_t); keysrc = f"由和弦估计（调内和弦覆盖 {round(ks * 100)}% 时长）"
        key_cov = None
        if source == "template" and H:
            sk = spec_key([(h[4], h[5], h[1] - h[0]) for h in H])
            if sk:
                key_cov = sk[2]
                tonic, mode = sk[0], sk[1]
                keysrc = f"按规范 v0 定调（大小三和弦落在该调自然音阶里的时长占 {round(key_cov * 100)}%）"
                if key_cov < 0.6:
                    keysrc += "，低于 0.6，不标级数、不参与和弦检索"
        key = (tonic, mode); gk = key_name(*key)
        if source == "template":     # spec: confidence is coverage, not the bass; the gate is the key coverage
            tier = None if key_cov is None or key_cov < 0.6 else ("高" if key_cov >= 0.8 else "中")
        # ---- chord segments (searchable only for tier 高/中) ----
        segs = []
        if H and tier in ("高", "中"):
            for idx, h in enumerate(H):
                typ, bpc = h[5], h[7]
                if bpc == h[4]: bpc = None
                rn = roman(h[4], typ, tonic) + (" on " + bass_degree(bpc, tonic) if bpc is not None else "")
                rx, rkind = rn, None
                if source == "template":     # display numeral per spec: quality only, no extensions
                    sr, rkind = spec_roman(h[4], typ, tonic, mode)
                    base = base_numeral(h[4], typ, tonic)
                    if typ in DIM_T and not base.endswith("°"): base = base.lower() + "°"
                    rn = (sr or base) + (" on " + bass_degree(bpc, tonic) if bpc is not None else "")
                segs.append(dict(rx=rx, rkind=rkind, root=h[4], type=typ, bass=bpc, cov=h[8], startBeat=h[2], endBeat=h[3], startSec=h[0], endSec=h[1],
                                 chord=chord_name(h[4], typ, bpc, key), roman=rn,
                                 diatonic=is_diatonic(h[4], typ, tonic, mode), ev=seg_ev[idx], tags=[]))
            # merge consecutive identical chords (half-bar grid repeats)
            merged = []
            for s in segs:
                if merged and merged[-1]["root"] == s["root"] and merged[-1]["type"] == s["type"] and merged[-1]["bass"] == s["bass"] and abs(merged[-1]["endBeat"] - s["startBeat"]) < 0.01:
                    m = merged[-1]; m["endBeat"] = s["endBeat"]; m["endSec"] = s["endSec"]; m["ev"] = (m["ev"][0] + s["ev"][0], m["ev"][1] + s["ev"][1])
                else: merged.append(s)
            segs = merged
            for i, s in enumerate(segs):
                s["bar"] = bar_of(s["startBeat"])
                if source == "template" and (s["cov"] or 0) >= 0.9 and s["endSec"] - s["startSec"] >= 0.8:
                    if s["type"] in COLOR_TAG: s["tags"].append(f"候选色彩:{COLOR_TAG[s['type']]}")
                    if s["bass"] is not None:
                        inv = s["bass"] in {(s["root"] + i) % 12 for i in CHORD_TYPES[s["type"]]}
                        s["tags"].append("候选低音:转位" if inv else "候选低音:分割和弦（on chord）")
                    s["evidence"] = f"证据：模板覆盖 {round((s['cov'] or 0) * 100)}%，本和弦区间贝斯起音 {s['ev'][1]} 个、落在根音 {s['ev'][0]} 个"
                if s["diatonic"]: continue
                # evidence gate: the bass must actually sit on this root (>= 2 onsets, >= 60 % on the root)
                if s["ev"][1] < 2 or s["ev"][0] / s["ev"][1] < 0.6: continue
                deg = (s["root"] - tonic) % 12
                nxt = segs[i + 1] if i + 1 < len(segs) else None
                ev_txt = f"证据：本和弦区间贝斯起音 {s['ev'][1]} 个，落在根音 {s['ev'][0]} 个"
                f = fam(s["type"]); fm = "M" if f in ("M", "s") else f
                if f == "M" and s["type"] not in ("maj7", "maj9", "6", "aug") and nxt and (s["root"] + 5) % 12 == nxt["root"]:
                    lab = "疑似副属和弦（三和弦形态，下一和弦在下方五度）"
                elif mode == "major" and ((deg, fm) in ((8, "M"), (10, "M"), (5, "m"), (3, "M"), (0, "m"))):
                    lab = "疑似借用和弦（同主音小调）"
                elif mode == "minor" and ((deg, fm) in ((5, "M"), (0, "M"), (9, "m"))):
                    lab = "疑似借用和弦（同主音大调）"
                elif deg in (3, 4, 8, 9) and fm == "M":
                    lab = "疑似半音关系三和弦"
                else:
                    continue                                  # no musical reading -> not offered as a candidate
                s["tags"].append(f"候选:{lab}"); s["evidence"] = ev_txt + (f"，模板覆盖 {round((s['cov'] or 0) * 100)}%" if s["cov"] is not None else "")
        seq = [dict(r=s["roman"], rx=s.get("rx", s["roman"]), rk=s.get("rkind"), cov=s.get("cov"), c=s["chord"], bar=s["bar"], root=(s["root"] - tonic) % 12, typ=s["type"], mode=mode, key=gk) for s in segs]
        named = []
        if segs:
            col, idxs = [], []
            for i, s in enumerate(segs):
                deg = (s["root"] - tonic) % 12
                if mode == "minor": deg = (deg + 9) % 12   # degree in the relative major
                f = fam(s["type"]); fm = "M" if f in ("M", "s") else f
                if col and col[-1] == (deg, fm): idxs[-1].append(i); continue
                col.append((deg, fm)); idxs.append([i])
            for i, nm, L in match_named(col):
                a, z = segs[idxs[i][0]], segs[idxs[i + L - 1][-1]]
                named.append(dict(name=nm, startBar=a["bar"], endBar=z["bar"]))
        # ---- bars ----
        by_bar = collections.defaultdict(list)
        for e in line:
            if e[2] >= 0: by_bar[bar_of(e[2])].append(e)
        seg_starts = [s["startBeat"] for s in segs]
        out_bars = []
        for bi, b in enumerate(bars):
            bn = b["bar"]; B0, L = b["startBeat"], b["lengthBeats"]
            ch = []
            if segs:
                i = 0 if bi == 0 else max(0, bisect.bisect_right(seg_starts, B0 + 1e-6) - 1)
                last = bi == len(bars) - 1                          # the last bar also holds chords running past the last bar line
                while i < len(segs) and (segs[i]["startBeat"] < B0 + L - 1e-6 or last):
                    s = segs[i]
                    if s["endBeat"] > B0 + 1e-6 or bi == 0:        # bar 1 also holds chords that end before beat 0
                        ch.append(dict(i=i, c=s["chord"], r=s["roman"], pos=round(max(s["startBeat"], B0) - B0, 3), tags=list(s["tags"]),
                                       ev=s.get("evidence")))
                    i += 1
            bs = bass_bar(b, by_bar.get(bn, []), segs, seg_starts, by_bar.get(bn + 1, []), name in SUSPECT_METER,
                          bass_block or ("这一小节的小节线不规则，不做律动检索" if b["irregular"] else None))
            pre = min((x["startSec"] for x in segs if x["startBeat"] < 0), default=None) if bi == 0 else None
            out_bars.append(dict(b=bn, t=round(b["startSec"], 3), e=round(b["endSec"], 3), **({"pre": round(pre, 3)} if pre is not None and pre < b["startSec"] else {}), len=L, ts=f'{b["numerator"]}/{b["denominator"]}', k=gk, kx=None,
                                 ch=ch, bs=bs, ml={}, caution=bn in caution or name in SUSPECT_METER))
        meta = d["song"]
        sid = "wb_" + name
        songs.append(dict(id=sid, kind="audio", title=meta["title"], artist=meta["artist"], group="音频（Workbench 第一批）",
                          src=audio_path(baseline, name) if local else None, srcRel=audio_rel(baseline, name, site),
                          origName=meta["originalFileName"], mel=[], globalKey=gk, keySource=keysrc, keyFit=round(ks, 2),
                          modulations=[], named=named, seq=seq, melodyPart=None,
                          bassParts=["贝斯（basic_pitch 转写，按八分音符取位）"], nbars=len(out_bars),
                          bpm=round(sorted(t[1] for t in tim["tempoMap"])[len(tim["tempoMap"]) // 2], 1),
                          tier=tier, keyCoverage=None if key_cov is None else round(key_cov, 3), tierBasis="调覆盖" if source == "template" else "根音与贝斯一致率", rootAgree=None if agree is None else round(agree, 3), rootAgreeN=[hits, n], bassOnsets=len(bass_all),
                          meterSuspect=name in SUSPECT_METER, hasHarmony=bool(H), chartRetimed=bool(d.get("chartRetimed")) and source == "chart", bassGridFit=round(grid_fit, 2), bassGridChance=round(grid_chance, 2), swing=name in SWING, bassGrid=grid_used, irregularShare=round(irr_share, 2), bassSearchable=bass_ok and name not in SUSPECT_METER,
                          bars=out_bars))
        # transcribed notes are not shipped to the page: audio songs are heard as slices of the original recording
        report.append((name, tier, None if agree is None else round(agree * 100, 1), gk, len(seq), len(named), len(out_bars), round(grid_fit, 2), round(grid_chance, 2), bass_ok))
    return songs, report


def audio_path(baseline, name):
    for ext in ("ogg", "flac", "wav", "mp3", "m4a"):
        p = os.path.join(baseline, name, "sources", "audio", "master." + ext)
        if os.path.exists(p): return p
    return os.path.join(baseline, name, "sources", "audio", "master.ogg")


def audio_rel(baseline, name, site):
    """URL of the recording relative to the page, for when the page is served together with the baseline folder"""
    p = audio_path(baseline, name)
    if not site or not os.path.exists(p): return None
    try:
        rel = os.path.relpath(p, site)
    except ValueError:              # different drive on Windows
        return None
    return "/".join(quote(x) for x in rel.replace("\\", "/").split("/"))


def bass_bar(bar, notes, segs, seg_starts, nxt, suspect, block=None):
    if suspect:
        return dict(onsets=0, grid="", tags=[], note="拍号存疑，不做小节级")
    if block:
        return dict(onsets=0, grid="", tags=[], note=block)
    L = bar["lengthBeats"]; steps = int(round(L * 4)); grid = ["."] * steps
    f = dict(onsets=len(notes))
    if not notes:
        f.update(grid="".join(grid), tags=["无贝斯"]); return f
    root = chordt = 0
    for e in notes:
        pos8 = int(round((e[2] - bar["startBeat"]) * 2))          # nearest 8th
        p = max(0, pos8 * 2)
        if p < steps: grid[p] = "x"          # an onset snapping onto the next downbeat is not drawn here
        if segs:
            i = bisect.bisect_right(seg_starts, e[2]) - 1
            if i >= 0 and e[2] < segs[i]["endBeat"]:
                s = segs[i]; pc = e[4] % 12
                root += pc == s["root"]; chordt += pc in {(s["root"] + t) % 12 for t in ((0, 4, 7) if s["type"] == "" else (0, 3, 7))}
    N = len(notes); per_beat = N / max(L, 1)
    iv = [b[4] - a[4] for a, b in zip(notes, notes[1:])]
    octave = sum(1 for x in iv if abs(x) == 12); chrom = sum(1 for x in iv if abs(x) == 1)
    offbeat8 = sum(1 for e in notes if int(round((e[2] - bar["startBeat"]) * 2)) % 2 == 1)
    f.update(grid="".join(grid), rootRatio=round(root / N, 2) if segs else None, chordToneRatio=round(chordt / N, 2) if segs else None,
             octaveJumps=octave, chromaticSteps=chrom, offbeat8=offbeat8)
    tags = []
    if per_beat >= 1.75 and offbeat8 >= N * 0.4: tags.append("8 分推进")
    elif per_beat <= 0.5: tags.append("长音铺底")
    if segs and root / N >= 0.85 and N >= 4: tags.append("根音为主")
    if octave >= 2: tags.append("八度跳跃")
    if nxt and abs(nxt[0][4] - notes[-1][4]) == 1: tags.append("半音接近下一小节")
    if chrom >= 2: tags.append("半音经过音")
    f["tags"] = tags
    return f


