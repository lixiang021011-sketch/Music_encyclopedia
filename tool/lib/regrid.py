"""Drum-anchored bar grid and chord-chart re-timing (optional step).

The upstream chart sits on a constant grid; in songs where that grid drifts, the chart lands on the wrong
half-bars. Here we rebuild beats and bar lines from the drums (find the steadiest ~10 bars, lock tempo and
phase there, extend beat by beat both ways), then move the chart cells onto the new half-bars by the integer
shift that best puts the bass on the chord root. The result is adopted only when it improves BOTH the
bass-root agreement and the agreement with the template reading by >= ADOPT points; otherwise the upstream
chart is kept unchanged.
"""
import bisect, collections, math

ADOPT = 0.03
W = {"kick": 1.0, "snare": 1.0, "closed hi-hat": 0.6, "crash": 0.8, "low-mid tom": 0.3}


# ---------- upstream bar lines ----------
def bars_of(s):
    tm = sorted(s["timing"]["tempoMap"]); b0 = [b for b, _ in tm]; secs = [s["timing"]["beat0"]]
    for i in range(1, len(tm)): secs.append(secs[-1] + (tm[i][0] - tm[i - 1][0]) * 60 / tm[i - 1][1])
    def b2s(beat):
        i = max(0, bisect.bisect_right(b0, beat) - 1); return secs[i] + (beat - tm[i][0]) * 60 / tm[i][1]
    irr = {x["bar"]: x["kind"] for x in s["timing"]["irregularBars"]}
    out = []
    for k, m in enumerate(sorted(s["timing"]["meterMap"])):
        if irr.get(k + 1) == "duplicate-bar-line": continue
        out.append(b2s(m[0]))
    return out


# ---------- beat tracking from the drums ----------
def onsets(s):
    return sorted((e[0], W.get(e[7], 0.3), e[7]) for e in s["events"] if e[6] == "drums")


def strength(ons, t0, t1, P, phase):
    sc = n = 0.0
    for t, w, _ in ons:
        if t < t0 or t >= t1: continue
        x = ((t - phase) / (P / 2)) % 1.0; d = min(x, 1 - x) * P / 2
        sc += w * math.exp(-(d / 0.025) ** 2); n += w
    return sc / n if n else 0


def anchor(ons, P0, bars_len=10):
    span = bars_len * 4 * P0; best = None
    t_end = ons[-1][0]; t = ons[0][0]
    while t + span < t_end:
        lo, hi = bisect.bisect_left(ons, (t,)), bisect.bisect_left(ons, (t + span,))
        sub = ons[lo:hi]
        for p in [P0 * (1 + k / 400) for k in range(-16, 17)]:
            for ph in [t + i * p / 24 for i in range(24)]:
                v = strength(sub, t, t + span, p, ph)
                if best is None or v > best[0]: best = (v, t, p, ph)
        t += span / 2
    return best


def extend(ons, start, P, t_lo, t_hi, direction, steer):
    beats = []; b = start; p = P
    times = [o[0] for o in ons]
    while t_lo <= b <= t_hi:
        beats.append(b)
        pred = b + direction * p
        lo, hi = bisect.bisect_left(times, pred - 0.12 * p), bisect.bisect_right(times, pred + 0.12 * p)
        cand = [(ons[i][0] - pred, ons[i][1] * math.exp(-((ons[i][0] - pred) / (0.06 * p)) ** 2)) for i in range(lo, hi) if ons[i][2] in steer]
        if cand:
            if sum(w for _, w in cand) < 1e-6: b = pred; continue
            off = sum(d * w for d, w in cand) / sum(w for _, w in cand)
            pred += 0.6 * off
            p = 0.92 * p + 0.08 * abs(pred - b)
        b = pred
    return beats


def track(s, bar_beats=4):
    ons = onsets(s); L = bars_of(s)
    if len(ons) < 50 or len(L) < 8: return None
    P0 = sorted(b - a for a, b in zip(L, L[1:]))[len(L) // 2] / bar_beats
    best = anchor(ons, P0)
    if best is None: return None
    v, t0, P, ph = best
    def onbeat(phase):
        sc = 0.0
        for t, w, nm in ons:
            if nm not in ("kick", "snare") or not (t0 <= t < t0 + 40 * P): continue
            x = ((t - phase) / P) % 1.0; d = min(x, 1 - x) * P
            sc += math.exp(-(d / 0.03) ** 2)
        return sc
    if onbeat(ph + P / 2) > onbeat(ph): ph += P / 2
    steer = set()
    for nm in ("kick", "snare", "crash", "closed hi-hat"):
        ts = [t for t, w, n2 in ons if n2 == nm and t0 <= t < t0 + 40 * P]
        if len(ts) < 8: continue
        on = sum(1 for t in ts if min(((t - ph) / P) % 1.0, 1 - ((t - ph) / P) % 1.0) * P < 0.06 * P)
        if on / len(ts) >= 0.6: steer.add(nm)
    if not steer: steer = {"snare", "crash"}
    allt = [e[0] for e in s["events"]]
    lo, hi = min(allt) - P, max(max(allt), ons[-1][0]) + P
    fwd = extend(ons, ph, P, lo, hi, +1, steer)
    bwd = extend(ons, ph, P, lo, hi, -1, steer)
    return sorted(set(bwd[1:] + fwd))


# ---------- bar lines (downbeat phase) ----------
def beat_features(s, beats):
    line = []
    for t, p in sorted((e[0], e[4]) for e in s["events"] if e[6] == "bass" and e[4] is not None):
        if line and t - line[-1][0] < 0.03: continue
        line.append((t, p))
    feat = [[0.0, 0.0, 0.0, 0.0] for _ in beats]
    def near(t, tol=0.09):
        i = bisect.bisect_left(beats, t)
        c = [j for j in (i - 1, i) if 0 <= j < len(beats)]
        if not c: return None
        j = min(c, key=lambda j: abs(beats[j] - t))
        return j if abs(beats[j] - t) <= tol else None
    prev = None
    for t, p in line:
        j = near(t)
        if j is not None and prev is not None and p % 12 != prev % 12: feat[j][0] = 1.0
        prev = p
    for t, name in [(e[0], e[7]) for e in s["events"] if e[6] == "drums"]:
        j = near(t, 0.06)
        if j is None: continue
        k = {"crash": 1, "kick": 2, "snare": 3}.get(name)
        if k: feat[j][k] = 1.0
    return [f[0] * 1.0 + f[1] * 1.0 + f[2] * 0.4 - f[3] * 0.7 for f in feat]


def phases(score, win=32, step=4):
    out = []
    for a in range(0, max(1, len(score) - win + 1), step):
        sums = [sum(score[j] for j in range(a, min(a + win, len(score))) if j % 4 == ph) for ph in range(4)]
        order = sorted(range(4), key=lambda k: -sums[k])
        out.append((a, order[0], sums[order[0]] - sums[order[1]]))
    return out


def bar_lines(beats, ph_windows):
    best, cur = None, None
    for i, (a, ph, m) in enumerate(ph_windows):
        if m < 1.0: cur = None; continue
        cur = (cur[0], i, ph) if cur and cur[2] == ph else (i, i, ph)
        if best is None or cur[1] - cur[0] > best[1] - best[0]: best = cur
    if best is None: best = (0, 0, max(range(4), key=lambda k: sum(1 for w in ph_windows if w[1] == k)))
    phase_at = [None] * len(ph_windows)
    for i in range(best[0], best[1] + 1): phase_at[i] = best[2]
    for direction in (+1, -1):
        cur = best[2]; i = best[1] + 1 if direction > 0 else best[0] - 1
        while 0 <= i < len(ph_windows):
            a, ph, m = ph_windows[i]
            j = i + direction
            nxt = ph_windows[j] if 0 <= j < len(ph_windows) else None
            if ph != cur and m >= 1.5 and nxt and nxt[1] == ph and nxt[2] >= 1.5: cur = ph
            phase_at[i] = cur; i += direction
    lines = []
    for b in range(len(beats)):
        w = min(len(ph_windows) - 1, max(0, (b - 16) // 4))
        if b % 4 == phase_at[w]:
            if lines and b - lines[-1] < 2: continue
            lines.append(b)
    return [beats[b] for b in lines]


# ---------- chart re-timing ----------
def chart_cells(s):
    H = s["harmony"]
    durs = sorted(h[1] - h[0] for h in H); cell = durs[len(durs) // 4]
    out = []
    for h in H:
        n = max(1, round((h[1] - h[0]) / cell))
        for k in range(n): out.append((h[0] + k * (h[1] - h[0]) / n, h[4], h[5]))
    return out


def half_bars(lines, beats):
    hb = []
    for a, b in zip(lines, lines[1:]):
        i, j = beats.index(a), beats.index(b)
        mid = beats[i + (j - i) // 2] if j - i >= 2 else (a + b) / 2
        hb += [(a, mid), (mid, b)]
    return hb


def bass_line(s):
    out = []
    for t, p in sorted((e[0], e[4]) for e in s["events"] if e[6] == "bass" and e[4] is not None):
        if out and t - out[-1][0] < 0.03: continue
        out.append((t, p))
    return out


def retime_cells(s, beats, lines, win=16):
    cells = chart_cells(s)
    hb = half_bars(lines, beats); bass = bass_line(s); ts = [t for t, _ in bass]
    cb = []
    for a, b in hb:
        i, j = bisect.bisect_left(ts, a), bisect.bisect_left(ts, b)
        cb.append(collections.Counter(p % 12 for _, p in bass[i:j]))
    starts = [a for a, _ in hb]
    base = []
    for c in cells:
        k = bisect.bisect_left(starts, c[0])
        cand = [j for j in (k - 1, k) if 0 <= j < len(starts)]
        base.append(min(cand, key=lambda j: (abs(starts[j] - c[0]), j)))
    def score(i0, i1, sh):
        h = n = 0
        for i in range(i0, i1):
            k = base[i] + sh
            if 0 <= k < len(cb) and cb[k]:
                n += sum(cb[k].values()); h += cb[k][cells[i][1]]
        return h / n if n else 0
    wins = []
    for i0 in range(0, len(cells) - win + 1, win // 2):
        sc = sorted(((score(i0, i0 + win, sh), sh) for sh in range(-4, 5)), reverse=True)
        wins.append((i0, sc[0][1], sc[0][0], sc[0][0] - sc[1][0]))
    if not wins: return []
    best = cur = None
    for k, (i0, sh, v, m) in enumerate(wins):
        if m < 0.10: cur = None; continue
        cur = (cur[0], k, sh) if cur and cur[2] == sh else (k, k, sh)
        if best is None or cur[1] - cur[0] > best[1] - best[0]: best = cur
    if best is None: best = (0, 0, 0)
    shift_at = [None] * len(wins)
    for k in range(best[0], best[1] + 1): shift_at[k] = best[2]
    for d in (1, -1):
        c = best[2]; k = best[1] + 1 if d > 0 else best[0] - 1
        while 0 <= k < len(wins):
            nx = wins[k + d] if 0 <= k + d < len(wins) else None
            if wins[k][1] != c and wins[k][3] >= 0.1 and nx and nx[1] == wins[k][1]: c = wins[k][1]
            shift_at[k] = c; k += d
    # where two chart cells land on the same half-bar (the shift changed between them), keep the one
    # whose root the bass actually plays there; on a tie keep the later cell
    slot = {}
    for i, c in enumerate(cells):
        k = min(len(wins) - 1, max(0, (i - win // 4) // (win // 2)))
        t = base[i] + shift_at[k]
        if not 0 <= t < len(hb): continue
        if t in slot and cb[t][slot[t][0]] > cb[t][c[1]]: continue
        slot[t] = (c[1], c[2])
    return [(hb[t][0], hb[t][1], r, q) for t, (r, q) in sorted(slot.items())]


def root_in_bass(segs, bass_all):
    ts = [t for t, _ in bass_all]; h = n = 0
    for a, b, root, q in segs:
        i = bisect.bisect_left(ts, a)
        for t, p in bass_all[i:]:
            if t >= b: break
            n += 1; h += p % 12 == root
    return h / n if n else 0


def vs_template(segs, T):
    ts = [t[0] for t in T]; a = tot = 0
    for x0, x1, root, q in segs:
        t = x0
        while t < x1:
            j = bisect.bisect_right(ts, t) - 1
            if j >= 0 and t < T[j][1]: tot += 1; a += T[j][2] == root
            t += 0.1
    return a / tot if tot else 0


SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def retime(s):
    """returns dict(adopted, before, after, tb, ta, reason, harmony) ; harmony is the new row list when adopted"""
    if not s["harmony"]: return dict(adopted=False, reason="没有上游和弦图")
    if not s["template"]: return dict(adopted=False, reason="没有模板读法，无法双重验证")
    beats = track(s)
    if not beats: return dict(adopted=False, reason="鼓的起音太少，不能重建拍子")
    lines = bar_lines(beats, phases(beat_features(s, beats)))
    if len(lines) < 8: return dict(adopted=False, reason="重建出的小节线太少")
    new = retime_cells(s, beats, lines)
    allbass = sorted((e[0], e[4]) for e in s["events"] if e[6] == "bass" and e[4] is not None)
    old = [(h[0], h[1], h[4], h[5]) for h in s["harmony"]]
    r = dict(before=root_in_bass(old, allbass), after=root_in_bass(new, allbass),
             tb=vs_template(old, s["template"]), ta=vs_template(new, s["template"]))
    r["adopted"] = r["after"] - r["before"] >= ADOPT and r["ta"] - r["tb"] >= ADOPT
    if r["adopted"]:
        rows = []
        for a, b, root, q in new:
            if rows and rows[-1][4] == root and rows[-1][5] == q and abs(rows[-1][1] - a) < 1e-9:
                rows[-1][1] = b; continue
            rows.append([a, b, None, None, root, q, SHARP[root] + ("m" if q == "minor" else "")])
        r["harmony"] = rows
        r["reason"] = "两项一致率都提高 ≥ 3 个百分点，采用"
    else:
        r["reason"] = "没有同时提高两项一致率，保留上游和弦图"
    return r
