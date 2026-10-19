"""Which songs of the export go into the library.

Only songs listed in publish.json -> include are built. Everything else in the export is reported as a
candidate (with its missing parts and possible duplicates), never indexed. A listed song is still skipped
when it fails the `require` gate, so a re-export that breaks a song cannot slip it in half-empty.
"""
import json, os, re, unicodedata

AUDIO_EXT = ("ogg", "flac", "wav", "mp3", "m4a")


def load(path):
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    m.setdefault("include", []); m.setdefault("exclude", {}); m.setdefault("require", {})
    if isinstance(m["exclude"], list): m["exclude"] = {x: "" for x in m["exclude"]}
    return m


def has_audio(d):
    return any(os.path.exists(os.path.join(d, "sources", "audio", "master." + e)) for e in AUDIO_EXT)


def _norm(t):
    t = unicodedata.normalize("NFKC", t or "").lower()
    return re.sub(r"[\s\(\)（）\[\]【】・·\-_]+", "", t)


def song_meta(d):
    try:
        with open(os.path.join(d, "canonical", "song.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def gate(name, d, meta, req):
    """reasons this song cannot be published (empty list = ok)"""
    why = []
    if "_error" in meta: return [f"song.json 读不了（{meta['_error']}）"]
    if meta.get("songId") not in (None, name): why.append(f"目录名 {name} 和 songId {meta.get('songId')} 不一致")
    want = req.get("schemaVersion")
    if want and meta.get("schemaVersion") != want: why.append(f"契约版本是 {meta.get('schemaVersion')}，要求 {want}")
    health = meta.get("health"); state = health.get("state") if isinstance(health, dict) else health
    ok_health = req.get("allowHealth")
    if ok_health and state not in ok_health: why.append(f"health 是 {state}")
    extra = sorted(set(meta.get("missing") or []) - set(req.get("allowMissing", [])))
    if extra: why.append("缺少 " + "、".join(extra))
    if req.get("needAudio", True) and not has_audio(d): why.append("没有原曲音频")
    return why


def select(dirs, manifest):
    """dirs: [(name, path)] from the export. Returns (published [(name, path)], report dict)."""
    req = manifest["require"]; include = manifest["include"]; exclude = manifest["exclude"]
    present = dict(dirs)
    meta = {n: song_meta(d) for n, d in dirs}
    key = lambda n: (_norm(meta[n].get("title")), _norm(meta[n].get("artist")))
    published, skipped, seen = [], [], {}
    for n in include:
        if n in exclude:
            skipped.append(dict(song=n, reason=f"同时在 include 和 exclude 里，按 exclude 处理：{exclude[n]}".rstrip("：")))
            continue
        if n not in present:
            skipped.append(dict(song=n, reason="导出里没有这首")); continue
        why = gate(n, present[n], meta[n], req)
        k = key(n) if "_error" not in meta[n] else None
        if k and k[0] and k in seen: why.append(f"和已发布的 {seen[k]} 标题、歌手相同（重复）")
        if why:
            skipped.append(dict(song=n, reason="；".join(why))); continue
        if k and k[0]: seen[k] = n
        published.append((n, present[n]))
    pub = {n for n, _ in published}
    candidates = []
    for n, d in dirs:
        if n in pub or n in include: continue
        if n in exclude:
            continue
        why = gate(n, d, meta[n], req)
        k = key(n) if "_error" not in meta[n] else None
        dup = seen.get(k) if k and k[0] else None
        candidates.append(dict(song=n, title=meta[n].get("title"), artist=meta[n].get("artist"),
                               missing=meta[n].get("missing") or [], duplicateOf=dup,
                               wouldPass=not why and not dup, problems=why))
    # duplicates among the candidates themselves
    groups = {}
    for c in candidates:
        if "_error" in meta[c["song"]]: continue
        k = key(c["song"])
        if k[0]: groups.setdefault(k, []).append(c["song"])
    for c in candidates:
        if c["duplicateOf"] is None and "_error" not in meta[c["song"]]:
            g = groups.get(key(c["song"]), [])
            if len(g) > 1: c["sameTitleAs"] = [x for x in g if x != c["song"]]
    excluded = [dict(song=n, reason=r) for n, r in exclude.items() if n in present]
    return published, dict(skipped=skipped, candidates=candidates, excluded=excluded)
