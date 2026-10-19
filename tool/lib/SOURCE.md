# lib/ 里各文件的来源

这些代码最早写在编曲例句库的工作区里，有几份还以"方法论资产"的形式交给过 Workbench。下表记下每个文件的来源和改动。**以本文件夹的版本为准**；别处的副本只作参考，不会自动同步。

| 文件 | 来源 | 和来源的关系 | 来源 sha1（2026-10-11） |
|---|---|---|---|
| `harmony.py` | 57 首 MIDI 版的 `harmony.py`；交给 Workbench 的方法论资产里的 `harmony.py` 是同一份 | 原样复制 | `b1b3fd5942ef868de135ddfa4b3bdc2117cf2ba1` |
| `audio_index.py` | 方法论资产里的 `build_audio_index.py` | 改成函数 `build(data, …)`，深拷贝输入；摇摆、拍号存疑名单从 config.json 读；`src` 只在本地版写入，另加 `srcRel`；弱起和弦归第 1 小节并记 `pre`；每小节写真实结束秒 `e`；最后一小节用 `barSeconds` | `8b649b6c30b4cb339991692b2fe2fd615d8f840f` |
| `regrid.py` | 方法论资产里的 `track.py`、`downbeat.py`、`retime.py`（加上工作区里的 `common.py`） | 合成一个文件，改成传入歌曲数据而不是读全局文件；锚点搜索只看窗口内的鼓点（结果不变，更快）；两个和弦图格子落到同一个半小节时，保留贝斯真正在弹的根音；加上"两项一致率都提高 ≥ 3 个百分点才采用"的判定 | track `28d4a88064b5…`、downbeat `15130b84799…`、retime `425a3b648f3f…`、common `ca7d14a0c043…` |
| `extract.py` | 工作区里的 `extract.py`（当时从导出生成中间 JSON 用） | 改成按歌读取，不写文件；加 `fingerprint`（轮询用）和 `content_hash`（缓存和版本号用） | — |
| `publish.py`、`lock.py` | 新写 | — | — |

## 改这些文件时

- 改了任何文件，构建版本号（`codeHash`）会变，页面会提示索引过时，下次构建自动全部重算。
- 改了 `regrid.py`，重对位缓存整份作废。
- 想把改动同步回方法论资产，按上表反向对照；`harmony.py` 是唯一需要逐字一致的一份。
