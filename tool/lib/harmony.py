"""Harmony analysis on canonical song data (L3, inferred).

Beat-level chord recognition (template scoring + Viterbi smoothing), local key (bar-level Viterbi over 24 keys),
roman numerals relative to the local key, and tags for advanced harmony (secondary dominants, borrowed chords,
tritone substitutes, chromatic mediants, on-chords, pedal points, chromatic bass lines, modulations, named
progressions).
"""
import math, collections

PC = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

CHORD_TYPES = collections.OrderedDict([
    ("", (0, 4, 7)), ("m", (0, 3, 7)), ("5", (0, 7)), ("7", (0, 4, 7, 10)), ("maj7", (0, 4, 7, 11)),
    ("m7", (0, 3, 7, 10)), ("6", (0, 4, 7, 9)), ("m6", (0, 3, 7, 9)), ("add9", (0, 2, 4, 7)), ("madd9", (0, 2, 3, 7)),
    ("sus4", (0, 5, 7)), ("sus2", (0, 2, 7)), ("7sus4", (0, 5, 7, 10)), ("dim", (0, 3, 6)), ("m7b5", (0, 3, 6, 10)),
    ("dim7", (0, 3, 6, 9)), ("aug", (0, 4, 8)), ("maj9", (0, 2, 4, 7, 11)), ("m9", (0, 2, 3, 7, 10)), ("9", (0, 2, 4, 7, 10)),
    ("mM7", (0, 3, 7, 11)), ("7b9", (0, 4, 7, 10, 1)),
])
MAJOR_FAMILY = {"", "7", "maj7", "6", "add9", "maj9", "9", "aug", "7b9"}
MINOR_FAMILY = {"m", "m7", "m6", "madd9", "m9", "mM7"}
DIM_FAMILY = {"dim", "m7b5", "dim7"}
COLOR_TAG = {"7": "属七", "maj7": "大七", "m7": "小七", "6": "六和弦", "m6": "六和弦", "add9": "加九", "madd9": "加九",
             "sus4": "挂四", "sus2": "挂二", "7sus4": "挂四", "dim": "减和弦", "m7b5": "半减七", "dim7": "减七",
             "aug": "增和弦", "maj9": "九和弦", "m9": "九和弦", "9": "九和弦", "mM7": "小大七", "5": "强力和弦", "7b9": "属七降九"}

# --- emission parameters (tuned by listening checks on known songs; see report) ---
ALPHA_OUT = 0.7      # penalty weight for profile mass outside the chord
BETA_MISSING = 0.10  # penalty per missing chord tone
GAMMA_SIZE = 0.08   # complexity penalty per tone beyond a triad
BASS_ROOT = 0.16
BASS_IN = 0.04
BASS_OUT = -0.06

MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
MINOR_NAT = [0, 2, 3, 5, 7, 8, 10]
KK_MAJ = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
KK_MIN = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
SHARP_MAJOR_TONICS = {7, 2, 9, 4, 11, 6}


def uses_sharps(key):
    if key is None: return False
    tonic, mode = key
    rel = tonic if mode == "major" else (tonic + 3) % 12
    return rel in SHARP_MAJOR_TONICS


def note_name(pc, key=None):
    return (SHARP_NAMES if uses_sharps(key) else FLAT_NAMES)[pc]


def chord_name(root, typ, bass=None, key=None):
    t = {"7b9": "7(b9)"}.get(typ, typ)
    n = note_name(root, key) + t
    if bass is not None and bass != root:
        n += "/" + note_name(bass, key)
    return n


def emission(profile, bass_pc, root, typ):
    tones = CHORD_TYPES[typ]
    pcs = [(root + t) % 12 for t in tones]
    inside = sum(profile[p] for p in pcs)
    outside = 1.0 - inside
    missing = 0.0
    for idx, (t, p) in enumerate(zip(tones, pcs)):
        core = t in (0, 3, 4, 7) or (typ == "7b9" and t in (10, 1)) or typ in ("sus4", "sus2", "dim", "aug", "5", "7sus4", "m7b5", "dim7")
        need = 0.03 if core else 0.12              # added tones (6th, 7th, 9th) must be clearly present
        if profile[p] < need:
            missing += 0.3 if t == 7 else 1.0      # a missing fifth is normal
    size = len(tones) - 3
    s = inside - ALPHA_OUT * outside - BETA_MISSING * missing - GAMMA_SIZE * max(size, 0)
    if typ in ("6", "m6"):
        s -= 0.03                                   # same notes as a m7 / m7b5 chord: prefer the 7th reading
    if typ == "5":
        s -= 0.06                                   # only wins when there is really no third
    if bass_pc is not None:
        if bass_pc == root: s += BASS_ROOT
        elif bass_pc in pcs: s += BASS_IN
        else: s += BASS_OUT
    return s


STATES = [(r, t) for r in range(12) for t in CHORD_TYPES]


def recognise_chords(beats):
    """beats: list of dict(profile[12] normalised or None, bass, barStart(bool), half(bool)). Returns state per beat."""
    n = len(beats)
    S = len(STATES)
    NONE = S  # index for "no chord"
    prev = [0.0] * (S + 1)
    back = []
    for i, b in enumerate(beats):
        if b["profile"] is None:
            em = [-1.0] * S + [0.2]
        else:
            em = [emission(b["profile"], b["bass"], r, t) for r, t in STATES] + [-0.4]
        if i == 0:
            cur = em[:]
            back.append([0] * (S + 1))
        else:
            pen = 0.05 if b["barStart"] else (0.09 if b["half"] else 0.16)
            best_prev = max(range(S + 1), key=lambda k: prev[k])
            bp = prev[best_prev]
            cur, bk = [], []
            for k in range(S + 1):
                stay = prev[k]
                switch = bp - pen
                if stay >= switch:
                    cur.append(stay + em[k]); bk.append(k)
                else:
                    cur.append(switch + em[k]); bk.append(best_prev)
            back.append(bk)
        prev = cur
    k = max(range(S + 1), key=lambda j: prev[j])
    path = [k]
    for i in range(n - 1, 0, -1):
        k = back[i][k]
        path.append(k)
    path.reverse()
    return [None if k == NONE else STATES[k] for k in path]


def key_scores(profile):
    """correlation with 24 Krumhansl-Kessler profiles; returns list of (score, tonic, mode)."""
    tot = sum(profile)
    if tot <= 0: return [(0.0, t, m) for t in range(12) for m in ("major", "minor")]
    x = [v / tot for v in profile]
    mx = sum(x) / 12
    out = []
    for prof, mode in ((KK_MAJ, "major"), (KK_MIN, "minor")):
        mp = sum(prof) / 12
        for t in range(12):
            y = [prof[(i - t) % 12] for i in range(12)]
            num = sum((x[i] - mx) * (y[i] - mp) for i in range(12))
            den = math.sqrt(sum((x[i] - mx) ** 2 for i in range(12)) * sum((y[i] - mp) ** 2 for i in range(12)))
            out.append((num / den if den else 0.0, t, mode))
    return out


def local_keys(bar_profiles, global_key, window=4, switch_penalty=0.7, global_bias=0.08):
    """Viterbi over bars; emission = key correlation over a window of bars."""
    keys = [(t, m) for m in ("major", "minor") for t in range(12)]
    n = len(bar_profiles)
    em_all = []
    for i in range(n):
        agg = [0.0] * 12
        for j in range(max(0, i - window + 1), min(n, i + window + 1)):
            for p in range(12): agg[p] += bar_profiles[j][p]
        sc = {(t, m): s for s, t, m in key_scores(agg)} if sum(agg) > 0 else {k: 0.0 for k in keys}
        em_all.append([sc[k] + (global_bias if k == global_key else 0.0) for k in keys])
    prev = em_all[0][:] if n else []
    back = []
    for i in range(1, n):
        bp_k = max(range(len(keys)), key=lambda k: prev[k])
        cur, bk = [], []
        for k in range(len(keys)):
            if prev[k] >= prev[bp_k] - switch_penalty:
                cur.append(prev[k] + em_all[i][k]); bk.append(k)
            else:
                cur.append(prev[bp_k] - switch_penalty + em_all[i][k]); bk.append(bp_k)
        back.append(bk); prev = cur
    if not n: return []
    k = max(range(len(keys)), key=lambda j: prev[j])
    path = [k]
    for i in range(n - 2, -1, -1):
        k = back[i][k]; path.append(k)
    path.reverse()
    return [keys[k] for k in path]


ROMAN = ["I", "bII", "II", "bIII", "III", "IV", "#IV", "V", "bVI", "VI", "bVII", "VII"]


def roman(root, typ, tonic):
    deg = (root - tonic) % 12
    r = ROMAN[deg]
    if typ in MINOR_FAMILY or typ in DIM_FAMILY:
        r = r.lower().replace("bii", "bii")
    suffix = {"": "", "m": "", "5": "5", "7": "7", "maj7": "maj7", "m7": "7", "6": "6", "m6": "6", "add9": "add9",
              "madd9": "add9", "sus4": "sus4", "sus2": "sus2", "7sus4": "7sus4", "dim": "°", "m7b5": "ø7", "dim7": "°7",
              "aug": "+", "maj9": "maj9", "m9": "9", "9": "9", "mM7": "M7", "7b9": "7(b9)"}[typ]
    return r + suffix


def base_numeral(root, typ, tonic):
    r = ROMAN[(root - tonic) % 12]
    return r.lower() if (typ in MINOR_FAMILY or typ in DIM_FAMILY) else r


def bass_degree(bass, tonic):
    return ROMAN[(bass - tonic) % 12]


def is_diatonic(root, typ, tonic, mode):
    pcs = {(root - tonic + t) % 12 for t in CHORD_TYPES[typ]}
    if mode == "major":
        return pcs <= set(MAJOR_SCALE)
    nat = set(MINOR_NAT) | {9, 11}   # natural + harmonic + melodic minor (raised 6th and 7th belong to the key)
    if pcs <= nat: return True
    deg = (root - tonic) % 12
    # harmonic-minor dominant and leading-tone chords count as diatonic in minor
    if deg == 7 and typ in ("", "7", "sus4", "7sus4", "add9", "9"): return True
    if deg == 11 and typ in DIM_FAMILY: return True
    if deg == 8 and typ == "aug": return True
    return False


BORROWED_IN_MAJOR = {(3, "M"): "bIII（借自同主音小调）", (5, "m"): "iv（借自同主音小调）", (7, "m"): "v（借自同主音小调）",
                     (8, "M"): "bVI（借自同主音小调）", (10, "M"): "bVII（借自同主音小调/混合利底亚）",
                     (2, "d"): "ii°（借自同主音小调）", (1, "M"): "bII（那不勒斯）", (0, "m"): "i（借自同主音小调）"}
BORROWED_IN_MINOR = {(5, "M"): "IV（借自多利亚）", (0, "M"): "I（皮卡第三度/同主音大调）", (2, "m"): "ii（借自多利亚）",
                     (1, "M"): "bII（那不勒斯）"}


def fam_borrow(typ):
    f = fam(typ)
    return "M" if f == "s" else f


def fam(typ):
    if typ in MAJOR_FAMILY: return "M"
    if typ in MINOR_FAMILY: return "m"
    if typ in DIM_FAMILY: return "d"
    if typ in ("sus4", "sus2", "7sus4", "5"): return "s"
    return "?"


# Named progressions, written in major-key degrees (minor keys are matched via their relative major)
NAMED = [
    ("王道进行 IV–V–iii–vi", [(5, "M"), (7, "M"), (4, "m"), (9, "m")]),
    ("丸サ进行 IVmaj7–III7–vi", [(5, "M"), (4, "M"), (9, "m")]),
    ("小室进行 vi–IV–V–I", [(9, "m"), (5, "M"), (7, "M"), (0, "M")]),
    ("卡农进行 I–V–vi–iii–IV", [(0, "M"), (7, "M"), (9, "m"), (4, "m"), (5, "M")]),
    ("I–V–vi–IV（Axis）", [(0, "M"), (7, "M"), (9, "m"), (5, "M")]),
    ("vi–IV–I–V（Axis 变体）", [(9, "m"), (5, "M"), (0, "M"), (7, "M")]),
    ("50 年代进行 I–vi–IV–V", [(0, "M"), (9, "m"), (5, "M"), (7, "M")]),
    ("ii–V–I", [(2, "m"), (7, "M"), (0, "M")]),
    ("iii–vi–ii–V（五度循环）", [(4, "m"), (9, "m"), (2, "m"), (7, "M")]),
    ("安达卢西亚终止 vi–V–IV–III", [(9, "m"), (7, "M"), (5, "M"), (4, "M")]),
    ("bVI–bVII–I（借用终止）", [(8, "M"), (10, "M"), (0, "M")]),
    ("bVII–IV–I（摇滚终止）", [(10, "M"), (5, "M"), (0, "M")]),
    ("IV–iv–I（小四级终止）", [(5, "M"), (5, "m"), (0, "M")]),
]


def match_named(seq):
    """seq: list of (degree_in_relative_major, family). Returns list of (start_index, name, length)."""
    hits = []
    for name, pat in NAMED:
        L = len(pat)
        for i in range(len(seq) - L + 1):
            ok = True
            for (d, f), (pd, pf) in zip(seq[i:i + L], pat):
                if d != pd or (f != pf and not (f == "s" and pf == "M")):
                    ok = False; break
            if ok:
                hits.append((i, name, L))
    return hits


def analyse(song_events, parts, timing, melody_part=None, bass_parts=()):
    """Returns dict with beats, chord segments (with tags), local key per bar, global key, modulations, named hits."""
    tpq = timing["ticksPerQuarter"]
    bars = timing["bars"]
    drums = {p["partId"] for p in parts if p["channel"] == 10}
    notes = [e for e in song_events if e["partId"] not in drums]
    # beat grid
    beats = []
    for b in bars:
        num, den = b["numerator"], b["denominator"]
        if den == 8 and num % 3 == 0 and num >= 6:
            nbeats, blen = num // 3, 1.5 * tpq
        else:
            nbeats, blen = num, 4 * tpq / den
        L = b["lengthTicks"]
        k = 0
        t = b["startTick"]
        while t < b["startTick"] + L - 1e-9:
            end = min(t + blen, b["startTick"] + L)
            beats.append(dict(bar=b["bar"], index=k, start=t, end=end, barStart=(k == 0),
                              half=(nbeats % 2 == 0 and k == nbeats // 2)))
            t = end; k += 1
    starts = [bt["start"] for bt in beats]
    import bisect
    prof = [[0.0] * 12 for _ in beats]
    bass_cands = [[] for _ in beats]
    low_cands = [[] for _ in beats]
    for e in notes:
        s, d = e["tick"], e["durationTicks"]
        en = s + max(d, 1)
        i = max(bisect.bisect_right(starts, s) - 1, 0)
        w_part = 0.25 if e["partId"] == melody_part else 1.0
        while i < len(beats) and beats[i]["start"] < en:
            bt = beats[i]
            ov = min(en, bt["end"]) - max(s, bt["start"])
            if ov > 0:
                w = ov / (bt["end"] - bt["start"]) * w_part * (1.3 if e["pitch"] < 55 else 1.0)
                prof[i][e["pitch"] % 12] += w
                cand = (e["pitch"], ov)
                if e["partId"] in bass_parts: bass_cands[i].append(cand)
                elif e["partId"] != melody_part: low_cands[i].append(cand)
            i += 1
    for i, bt in enumerate(beats):
        tot = sum(prof[i])
        bt["profile"] = [v / tot for v in prof[i]] if tot > 0.15 else None
        c = bass_cands[i] or [x for x in low_cands[i] if x[1] >= (bt["end"] - bt["start"]) * 0.25]
        if c:
            lowest = min(p for p, _ in c)
            bt["bassPitch"] = lowest
            bt["bass"] = lowest % 12
        else:
            bt["bassPitch"] = None; bt["bass"] = None
    states = recognise_chords(beats)
    # global key: duration-weighted pitch classes with bass emphasis
    g = [0.0] * 12
    for e in notes:
        g[e["pitch"] % 12] += e["durationBeats"] * (1.5 if e["partId"] in bass_parts else 1.0)
    gs = max(key_scores(g))
    global_key = (gs[1], gs[2])
    # bar profiles for local key
    bar_index = {b["bar"]: i for i, b in enumerate(bars)}
    bar_prof = [[0.0] * 12 for _ in bars]
    for bt in beats:
        if bt["profile"]:
            bi = bar_index[bt["bar"]]
            for p in range(12): bar_prof[bi][p] += bt["profile"][p]
    lk = local_keys(bar_prof, global_key)
    # short excursions (< 12 bars) that return to the previous key are tonicisations, not modulations:
    # analyse their chords in the surrounding key and tag the bars instead
    runs = []
    for i, k in enumerate(lk):
        if runs and runs[-1][0] == k: runs[-1][2] = i
        else: runs.append([k, i, i])
    excursion = {}
    changed = True
    while changed:
        changed = False
        runs = []
        for i, k in enumerate(lk):
            if runs and runs[-1][0] == k: runs[-1][2] = i
            else: runs.append([k, i, i])
        for r, (k, a, b) in enumerate(runs):
            L = b - a + 1
            prevk = runs[r - 1][0] if r > 0 else None
            nextk = runs[r + 1][0] if r + 1 < len(runs) else None
            if prevk is not None and prevk == nextk and L < 12:
                target = prevk                       # excursion that returns: tonicisation
            elif L < 8 and (prevk or nextk) is not None and len(runs) > 1:
                target = prevk if prevk is not None else nextk   # too short to call a key
            else:
                continue
            for i in range(a, b + 1):
                excursion[i] = excursion.get(i, k)
                lk[i] = target
            changed = True
            break
    bar_key = {b["bar"]: lk[i] for i, b in enumerate(bars)}
    bar_excursion = {bars[i]["bar"]: k for i, k in excursion.items()}
    # chord segments
    segs = []
    for i, (bt, st) in enumerate(zip(beats, states)):
        if segs and segs[-1]["state"] == st and bar_key[bt["bar"]] == segs[-1]["key"]:
            segs[-1]["endTick"] = bt["end"]; segs[-1]["beats"].append(i)
        else:
            segs.append(dict(state=st, startTick=bt["start"], endTick=bt["end"], bar=bt["bar"], beatInBar=bt["index"],
                             beats=[i], key=bar_key[bt["bar"]]))
    for sg in segs:
        bc = collections.Counter(beats[i]["bass"] for i in sg["beats"] if beats[i]["bass"] is not None)
        sg["bass"] = bc.most_common(1)[0][0] if bc else None
        sg["bassPitch"] = min((beats[i]["bassPitch"] for i in sg["beats"] if beats[i]["bassPitch"] is not None), default=None)
    segs = [s for s in segs if s["state"] is not None]
    # annotate
    for idx, sg in enumerate(segs):
        root, typ = sg["state"]
        tonic, mode = sg["key"]
        sg["chord"] = chord_name(root, typ, sg["bass"], sg["key"])
        sg["root"], sg["type"] = root, typ
        sg["roman"] = roman(root, typ, tonic)
        if sg["bass"] is not None and sg["bass"] != root:
            sg["roman"] += " on " + bass_degree(sg["bass"], tonic)
        sg["diatonic"] = is_diatonic(root, typ, tonic, mode)
        sg["tags"] = []
        if typ in COLOR_TAG: sg["tags"].append(("色彩", COLOR_TAG[typ]))
        if sg["bass"] is not None and sg["bass"] != root:
            if sg["bass"] in [(root + t) % 12 for t in CHORD_TYPES[typ]]:
                sg["tags"].append(("低音", "转位"))
            else:
                sg["tags"].append(("低音", "分割和弦（on chord）"))
    for idx, sg in enumerate(segs):
        nxt = next((s2 for s2 in segs[idx + 1:] if s2["root"] != sg["root"]), None)
        prv = segs[idx - 1] if idx > 0 else None
        root, typ = sg["root"], sg["type"]
        tonic, mode = sg["key"]
        deg = (root - tonic) % 12
        explained = sg["diatonic"]
        if nxt:
            nroman = base_numeral(nxt["root"], nxt["type"], tonic)
            # secondary dominant
            if fam(typ) == "M" and typ not in ("maj7", "aug", "6") and (root + 5) % 12 == nxt["root"] and \
                    (not sg["diatonic"] or typ in ("7", "9", "7b9")) and not (deg == 7 and (nxt["root"] - tonic) % 12 == 0):
                sg["tags"].append(("功能", f"副属和弦 V/{nroman.split(' on ')[0]}")); explained = True
            # secondary leading-tone chord
            elif typ in DIM_FAMILY and (root + 1) % 12 == nxt["root"] and not sg["diatonic"]:
                sg["tags"].append(("功能", f"副导和弦 vii°/{nroman.split(' on ')[0]}")); explained = True
            # tritone substitution
            elif typ in ("7", "9", "7b9", "") and (root - 1) % 12 == nxt["root"] and not sg["diatonic"]:
                sg["tags"].append(("功能", f"三全音替代 subV/{nroman.split(' on ')[0]}")); explained = True
        if not sg["diatonic"] and not explained and prv and prv["type"] in ("7", "9", "7b9"):
            alt = {(prv["root"] + t) % 12 for t in (0, 4, 7, 10, 1, 3, 8)}
            pcs = {(root + t) % 12 for t in CHORD_TYPES[typ]}
            if pcs <= alt:
                sg["tags"].append(("功能", "属和弦变化音（b9 / #9 / b13）")); explained = True
        if not sg["diatonic"] and not explained and deg == 0 and typ in ("7", "9", "7sus4") and mode == "major":
            sg["tags"].append(("功能", "属七化的主和弦 I7（布鲁斯色彩 / 通往 IV）")); explained = True
        if not sg["diatonic"] and not explained and mode == "major" and typ in ("7", "9", "7b9", "7sus4", "") \
                and deg in (2, 4, 9, 11):
            tgt = base_numeral((root + 5) % 12, "m" if deg in (2, 4, 9) and (root + 5 - tonic) % 12 in (2, 4, 9) else "", tonic)
            sg["tags"].append(("功能", f"副属和弦（未直接解决）V/{tgt}")); explained = True
        if not sg["diatonic"] and not explained and mode == "major" and deg == 6 and typ in ("m7b5", "dim", "dim7"):
            sg["tags"].append(("功能", "升四级半减七 #ivø7")); explained = True
        if not sg["diatonic"] and not explained and typ == "aug":
            sg["tags"].append(("功能", "增三和弦（经过性 / 半音线条）")); explained = True
        if not sg["diatonic"] and not explained:
            table = BORROWED_IN_MAJOR if mode == "major" else BORROWED_IN_MINOR
            hit = table.get((deg, fam_borrow(typ)))
            if hit:
                sg["tags"].append(("功能", "借用和弦 " + hit)); explained = True
        if not sg["diatonic"] and not explained and prv and fam(typ) == "M" and fam(prv["type"]) == "M" and \
                (root - prv["root"]) % 12 in (3, 4, 8, 9):
            sg["tags"].append(("功能", "半音关系三和弦（chromatic mediant）")); explained = True
        if not sg["diatonic"] and not explained:
            sg["tags"].append(("功能", "调外和弦（未归类）"))
    # pedal points and chromatic bass lines over segments
    i = 0
    while i < len(segs):
        j = i
        while j + 1 < len(segs) and segs[j + 1]["bass"] is not None and segs[j + 1]["bass"] == segs[i]["bass"]:
            j += 1
        if segs[i]["bass"] is not None and j - i + 1 >= 3 and len({segs[k]["root"] for k in range(i, j + 1)}) >= 2:
            for k in range(i, j + 1): segs[k]["tags"].append(("低音", "持续低音"))
        i = j + 1
    bp = [s["bassPitch"] for s in segs]
    for i in range(len(segs) - 2):
        a, b, c = bp[i], bp[i + 1], bp[i + 2]
        if None not in (a, b, c) and abs(b - a) == 1 and (b - a) == (c - b):
            for k in (i, i + 1, i + 2):
                if ("低音", "半音级进低音线") not in segs[k]["tags"]:
                    segs[k]["tags"].append(("低音", "半音级进低音线"))
    # modulations
    mods = []
    for a, b in zip(bars, bars[1:]):
        ka, kb = bar_key[a["bar"]], bar_key[b["bar"]]
        if ka != kb:
            relative = (ka[1] != kb[1]) and ((ka[0] + (3 if ka[1] == "minor" else -3)) % 12 == kb[0])
            parallel = ka[0] == kb[0] and ka[1] != kb[1]
            kind = "关系大小调切换" if relative else ("同主音大小调切换" if parallel else "转调")
            mods.append(dict(bar=b["bar"], fromKey=key_name(*ka), toKey=key_name(*kb),
                             kind=kind,
                             interval=(kb[0] - ka[0]) % 12))
    mod_at = {m["bar"]: m for m in mods}
    for sg in segs:
        m = mod_at.get(sg["bar"])
        if m and sg["beatInBar"] == 0:
            sg["tags"].append(("调性", m["kind"]))
        if sg["bar"] in bar_excursion:
            sg["tags"].append(("调性", "短暂离调（" + key_name(*bar_excursion[sg["bar"]]) + "）"))
    # named progressions (collapse repeats; degrees relative to relative major for minor keys)
    seq, owners = [], []
    for k, sg in enumerate(segs):
        tonic, mode = sg["key"]
        ref = tonic if mode == "major" else (tonic + 3) % 12
        item = ((sg["root"] - ref) % 12, fam(sg["type"]))
        if seq and seq[-1] == item:
            owners[-1].append(k); continue
        seq.append(item); owners.append([k])
    named = []
    for i, name, L in match_named(seq):
        ks = [k for grp in owners[i:i + L] for k in grp]
        named.append(dict(name=name, startSeg=ks[0], endSeg=ks[-1]))
        for k in ks:
            if ("进行", name) not in segs[k]["tags"]:
                segs[k]["tags"].append(("进行", name))
    return dict(beats=beats, segments=segs, globalKey=global_key, barKey=bar_key, modulations=mods, named=named,
                excursions={b: key_name(*k) for b, k in bar_excursion.items()})


def key_name(tonic, mode):
    return note_name(tonic, (tonic, mode)) + (" 大调" if mode == "major" else " 小调")
