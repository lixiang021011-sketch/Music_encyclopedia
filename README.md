# Music_encyclopedia · 原曲转写例句库

在原曲录音的机器转写里，按和声走向、调外和弦候选、贝斯律动和调性，查到具体是哪首歌的哪几小节，并直接播放原曲片段。数据来自 Workbench 的导出（`workbench_data_baseline`，workbench.music.v2 契约）。

## 第一次用

1. 把 `tool\config.local.example.json` 复制成 `tool\config.local.json`，在里面写上 Workbench 导出文件夹的路径（这个文件不进仓库）。
2. 双击 `rebuild.cmd` 构建一次，再双击 `open.cmd`。

## 说明

例句库从 `workbench_data_baseline` 生成，但**只收 `tool\publish.json` 里列出的歌**。Workbench 重新导出、新增歌曲，都不会自动进例句库：新歌先出现在"待审核"名单里，确认后加进发布名单再重建。

这个文件夹放在 Workbench 仓库外面，对导出只读，不改导出里的任何文件。

## 怎么用

| 想做的事 | 操作 |
|---|---|
| 打开例句库 | 双击 `open.cmd`。浏览器自动打开，原曲直接能播 |
| 看索引是不是最新 | 页面顶部会写这一版的版本号，以及"和当前导出一致"或"⚠ 索引过时" |
| 导出以后自动更新 | `open.cmd` 开着就行。已发布的歌在导出里有变化、而且导出已经写完，就自动重建；改了 `config.json`、`publish.json` 或构建程序也会重建 |
| 手动重建一次 | 双击 `rebuild.cmd` |
| 看导出里有哪些歌还没发布 | 双击 `candidates.cmd`，会列出每首能不能过门槛、和哪首重复 |
| 发布新歌 | 把目录名加进 `tool\publish.json` 的 `include`，再重建 |
| 跑测试 | 双击 `test.cmd` |

接进 Workbench 的批处理，在导出那一步后面加一行（同样只会重建已发布的歌）：

```powershell
python "<本仓库>\tool\build_library.py"
```

每次构建结束，控制台都会打印这一版的统计：发布了几首，各项检索有几首可用，跳过了几首，导出里还有几首待审核。这些数字只认 `build_report.json`，文档里不再写死。

## 发布名单 `tool\publish.json`

- `include`：要发布的歌（目录名）。现在是 Workbench 第一批审定的 34 首。
- `exclude`：明确不收的歌，并写明原因。现在排除的是重复歌：`yoru` 和已发布的 `yorukake` 重复；`kaishin2`、`new_55` 和 `kaishin` 重复。`kaishin` 本身留在待审核里，三份里保留哪一份要你来定。
- `require`：发布名单里的歌也要先过这道门槛才进索引。要求契约版本是 workbench.music.v2，health 在许可范围内，缺的组件只能是 `allowMissing` 里那几样（和声、段落、钢琴、吉他），而且要有原曲音频。没过门槛的歌会跳过，原因写进 `build_report.json` 的 `skipped`。
- 标题和歌手都相同的歌只收第一首，其余作为重复跳过。

## 版本和过时判断

- 每次构建都有一个版本号 `buildId`，由三样东西算出来：已发布歌曲输入文件的**内容哈希**、配置哈希（`config.json` 加上 `publish.json`）、构建程序哈希。输入相同，版本号就相同。
- `builtAt` 是带时区的 ISO 8601 时间。
- `_snapshot.json` 记下这一版用到的每首歌的文件指纹。`serve.py` 每 20 秒比对一次，结果写进 `_status.json`，页面顶部据此显示状态。
- 重对位缓存的键是"输入内容哈希 + `regrid.py` 的哈希"，改了重对位代码，缓存就整份作废。
- `build_report.json` 里的 `changesSinceLastBuild` 只比较这一版和上一版索引，不代表索引跟得上当前导出。跟不跟得上，看页面顶部的状态或 `_status.json`。

## 指标突降时不发布

和上一版相比，一首歌的贝斯起音数或根音一致率下降 30% 以上，这一版就**不更新这首歌**，两份索引里都沿用它的上一版，并在控制台、`build_report.json` 的 `heldBack` 和页面顶部报警。核实没问题后，用 `python build_library.py --accept-drops` 重建，接受新数据。

（这道闸是 10-18 那次导出之后加的：当时投票层把贝斯起音并掉了一大半，根音一致率成段塌掉。）

## 构建安全

- 同一时间只能有一个构建：自动重建和手动 `rebuild.cmd` 共用一个锁文件。
- 所有文件先写进 `site\.staging\`，全部成功后再逐个原子替换。快照最后写入，中途出错的话，这一版会被判为过时。
- 有一首歌读坏了，只跳过这一首，记进报告。

## 公开版和本地版

- 默认构建公开版：索引里不带本机绝对路径，音频只用相对于 `site\` 的路径 `srcRel`。
- `python build_library.py --local` 构建本地调试版，额外写入音频和导出的绝对路径。

## 文件夹里有什么

```
Music_encyclopedia\
  open.cmd / rebuild.cmd / candidates.cmd / test.cmd
  .gitignore
  tool\
    build_library.py     构建：选歌 → 读导出 → 重对位 → 两份索引 → 报告
    serve.py             本机服务器（只监听 127.0.0.1）+ 每 20 秒检查过时 + 自动重建
    publish.json         发布名单和门槛
    config.json          默认配置：端口、摇摆名单、拍号存疑名单
    config.local.json    本机路径（自己建，不进仓库）
    lib\                 extract、publish、regrid、audio_index、harmony、lock；来源见 lib\SOURCE.md
    page\index.html      页面模板
    tests\               不变量测试
  site\                  生成结果（不进版本管理）
```

## 服务器放行什么

只放行 `site\` 里的文件（以 `.` 开头的除外），以及**已发布歌曲**的 `sources\audio\master.*`。其余一律 404，包括导出里的数据文件、未发布歌曲的音频、路径穿越。支持 Range 请求，所以音频能跳到指定小节播放。

## 测试覆盖什么

`tests\test_library.py` 用一个合成的小导出完整构建，导出里有变速、换拍、弱起、最后一小节渐慢、重复歌、缺组件、坏 JSON 和不在名单里的新歌。检查的不变量包括：

- 每个和弦段的小节号都在 1 到总小节数之间，而且都能在某一小节的和弦列表里找到；
- 每小节的结束秒等于下一小节的开始秒，最后一小节按 `barSeconds` 计算；
- 分级和门槛一致，贝斯开放检索的歌都过了随机基线；
- 公开版索引里没有绝对路径；
- 只有发布名单里、过了门槛的歌进索引，跳过的原因都写进报告；
- 输入不变，版本号就不变；已发布的歌改了会判为过时，新出现的目录不会；
- 构建锁有效；服务器的 Range、路径穿越和放行规则符合预期。

如果 `site\` 里已经有构建结果，测试会再对真实索引查一遍同样的不变量。

## 和 Workbench 的接口

**Workbench 那边不需要改任何东西。** 以下情况改 `tool\config.json`，再重建：

- 鼓的摇摆标注变了：改 `swing`。
- 某首歌的拍号或速度修好了：从 `suspectMeter` 里删掉。

人声旋律和转调检测做好以后，只要按 v2 契约落在 `annotations/` 里，告诉我字段名，我在 `lib\extract.py` 和 `lib\audio_index.py` 里接上，页面的"旋律"页和调性就能打开。
