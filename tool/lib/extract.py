"""Read one song folder of workbench_data_baseline (workbench.music.v2) into the compact form the index
builder uses. Read-only: nothing in the Workbench export is touched."""
import hashlib, json, os


def _jsonl(path):
    if not os.path.exists(path): return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


INPUTS = ("canonical/song.json", "canonical/parts.json", "canonical/timing.json", "canonical/events.jsonl",
          "annotations/harmony.jsonl", "annotations/harmony_template.jsonl", "features/window_features_v1.json")


def fingerprint(d):
    """cheap change detector for polling: size + mtime in nanoseconds of every input file"""
    out = []
    for rel in INPUTS:
        p = os.path.join(d, rel)
        try:
            st = os.stat(p); out.append(f"{rel}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            out.append(f"{rel}:-")
    return "|".join(out)


def content_hash(d):
    """sha1 over the bytes of every input file (and which ones are absent): the cache key and the build's source hash"""
    h = hashlib.sha1()
    for rel in INPUTS:
        p = os.path.join(d, rel)
        h.update(rel.encode() + b"\0")
        if not os.path.exists(p):
            h.update(b"-\0"); continue
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()


def irregular_bars(tim):
    """irregularBars with `bar` = 1-based position in the beat-sorted meterMap.

    Exports differ in how they number these (1-based in the first batch, 0-based in later ones), so each entry is
    matched to its bar line by `atSeconds` when it has one. A meterMap entry with numerator 0 or less than half a
    beat is a duplicate bar line whether or not it is listed."""
    mm = sorted(tim["meterMap"], key=lambda m: m["beat"])
    tm = sorted(tim["tempoMap"], key=lambda t: t["beat"])
    secs = [tim["audioAlignment"]["beat0AudioSeconds"]]
    for i in range(1, len(tm)): secs.append(secs[-1] + (tm[i]["beat"] - tm[i - 1]["beat"]) * 60 / tm[i - 1]["bpm"])
    def b2s(beat):
        i = max(0, max((k for k, t in enumerate(tm) if t["beat"] <= beat), default=0))
        return secs[i] + (beat - tm[i]["beat"]) * 60 / tm[i]["bpm"]
    line_sec = [b2s(m["beat"]) for m in mm]
    out, seen = [], set()
    for x in tim.get("irregularBars", []):
        y = dict(x); y["barRaw"] = x.get("bar")
        if x.get("atSeconds") is not None and line_sec:
            k = min(range(len(line_sec)), key=lambda j: abs(line_sec[j] - x["atSeconds"]))
            if abs(line_sec[k] - x["atSeconds"]) < 0.05: y["bar"] = k + 1
        out.append(y); seen.add(y["bar"])
    for k, m in enumerate(mm):
        if (m.get("numerator") == 0 or (m.get("beatsExact") is not None and m["beatsExact"] < 0.5)) and k + 1 not in seen:
            out.append(dict(bar=k + 1, kind="duplicate-bar-line", note="numerator 0 / < 0.5 beat (found by the library)"))
    return out


def song_dirs(baseline):
    for name in sorted(os.listdir(baseline)):
        d = os.path.join(baseline, name)
        if name.startswith(("_", ".")) or not os.path.isdir(d): continue
        if not os.path.exists(os.path.join(d, "canonical", "timing.json")): continue
        yield name, d


def read_song(d):
    c = lambda *p: os.path.join(d, *p)
    song = _json(c("canonical", "song.json"))
    parts = _json(c("canonical", "parts.json"))
    role = {}
    for p in parts:
        r = [l["value"] for l in p.get("labels", []) if l.get("type") == "role"]
        role[p["partId"]] = r[0] if r else "other"
    tim = _json(c("canonical", "timing.json"))
    ev = []
    for e in _jsonl(c("canonical", "events.jsonl")):
        ev.append([round(e["startSeconds"], 4), round(e["endSeconds"], 4), round(e["startBeat"], 4), round(e["endBeat"], 4),
                   e.get("pitch"), e.get("velocity"), role.get(e["partId"], "other"), e.get("drum")])
    H = [[h["startSeconds"], h["endSeconds"], h.get("startBeat"), h.get("endBeat"), h["rootPitchClass"], h["quality"], h["symbol"]]
         for h in _jsonl(c("annotations", "harmony.jsonl"))]
    T = [[h["startSeconds"], h["endSeconds"], h["rootPitchClass"], h["quality"], h["symbol"], h.get("bassPitchClass"), h.get("coverage")]
         for h in _jsonl(c("annotations", "harmony_template.jsonl"))]
    windows = []
    wf = c("features", "window_features_v1.json")
    if os.path.exists(wf):
        windows = [[x["startBar"], x["lengthBars"], x["timing"]["timing"]] for x in _json(wf)["windows"]]
    health = song.get("health")
    return dict(song={k: song.get(k) for k in ("songId", "title", "artist", "originalFileName", "masterAudio", "missing")},
                health=health.get("state") if isinstance(health, dict) else health,
                schema=song.get("schemaVersion"),
                timing=dict(tempoMap=[[t["beat"], t["bpm"]] for t in tim["tempoMap"]],
                            meterMap=[[m["beat"], m["numerator"], m["denominator"], m.get("beatsExact"), m.get("barSeconds")] for m in tim["meterMap"]],
                            beat0=tim["audioAlignment"]["beat0AudioSeconds"], irregularBars=irregular_bars(tim)),
                events=ev, harmony=H, template=T, windows=windows)
