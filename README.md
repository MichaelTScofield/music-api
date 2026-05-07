# music-api

这是一个基于网易云音乐接口的本地 API 服务项目，并在此基础上增加了几个面向本地音乐整理和歌单创建的桌面工具。

项目准备推送到：

```text
https://github.com/MichaelTScofield/music-api.git
```

## 功能概览

- 网易云音乐 API 服务：基于 Express 提供搜索、歌曲、歌词、评论、歌单、专辑、用户等接口。
- 音乐歌单工具：按歌手抓取网易云/QQ 音乐专辑歌曲，并创建平台歌单。
- QQ 音乐歌单工具：按歌手或 Singer MID 创建 QQ 音乐歌单。
- 音乐整理工具：按专辑元数据整理本地音乐文件，并可对照网易云/QQ 音乐专辑信息。
- FLAC 转 MP3 工具：批量转换本地 FLAC 文件。
- 打包支持：使用 PyInstaller 将 GUI 工具打成 Windows 可执行程序。

## 目录结构

```text
.
├── app.js / index.js              # Node API 服务入口
├── server/                        # Express 服务启动与测试
├── routes/module/                 # 网易云音乐 API 路由模块
├── utils/                         # 请求、缓存、加密、Cookie 等工具
├── public/                        # 简单首页
├── music_playlist_tool/           # 音乐歌单创建 GUI/CLI 工具
├── auto_assign_tool/              # 本地音乐专辑整理工具
├── flac_to_mp3_tool/              # FLAC 转 MP3 GUI 工具
├── runtime/                       # 打包 GUI 时复制的内置 API 服务运行目录
└── service_manager.py             # GUI 内部启动/关闭本地 API 服务
```

## 环境要求

- Node.js 16+，用于运行网易云音乐 API 服务。
- Python 3.10+，用于运行和打包桌面工具。
- Windows 环境，GUI 工具和 `.bat` 打包脚本按 Windows 设计。
- 可选：PyInstaller，用于重新打包 GUI。
- 可选：ffmpeg，用于 FLAC 转 MP3 工具。

## 安装依赖

```bash
npm install
```

Python 工具依赖按本机环境安装，常用依赖包括：

```bash
pip install requests pyinstaller pillow pystray mutagen opencc-python-reimplemented
```

如果使用 FLAC 转 MP3 工具，还需要确保本机可访问 `ffmpeg`。

## 启动 API 服务

开发模式：

```bash
npm run dev
```

直接启动：

```bash
node app.js
```

默认监听地址：

```text
http://localhost:3001
```

健康检查：

```text
http://localhost:3001/hello
```

接口示例：

```text
http://localhost:3001/search?keywords=方大同
http://localhost:3001/song/url?id=33894312
http://localhost:3001/lyric?id=33894312
```

服务支持通过环境变量覆盖端口：

```bash
set PORT=3001
node app.js
```

## 音乐歌单工具

主工具入口：

```bash
python music_playlist_tool/gui_app.py
```

命令行入口：

```bash
python music_playlist_tool/auto.py "歌手名"
```

常用参数：

```bash
python music_playlist_tool/auto.py "歌手名" --mode auto
python music_playlist_tool/auto.py "歌手名" --mode qq
python music_playlist_tool/auto.py "歌手名" --mode netease
python music_playlist_tool/auto.py "歌手名" --refresh-qq-cookie
```

模式说明：

- `auto`：对比网易云和 QQ 音乐专辑数量，自动选择专辑数更多的平台创建歌单。
- `qq`：只创建 QQ 音乐歌单。
- `netease`：只创建网易云歌单。

Cookie 说明：

- 网易云 Cookie 通过 GUI 中的“更新网易云 Cookie”按钮手动粘贴保存。
- QQ 音乐 Cookie 通过 GUI 中的“更新 QQ Cookie”按钮手动粘贴保存。
- Cookie 文件包含个人登录凭据，不要提交到 Git 仓库。

## QQ 音乐歌单工具

仅使用 QQ 音乐的 GUI：

```bash
python music_playlist_tool/qq_gui_app.py
```

命令行：

```bash
python music_playlist_tool/qq-auto.py "歌手名"
python music_playlist_tool/qq-auto.py "歌手名" --singer-mid 003Nz2So3XXYek
python music_playlist_tool/qq-auto.py --refresh-cookie
```

`--singer-mid` 可用于同名歌手或搜索结果不准确的场景。

## 本地音乐整理工具

网易云/通用版本：

```bash
python auto_assign_tool/auto-assign.py
```

QQ 音乐版本：

```bash
python auto_assign_tool/qq-auto-assign.py
```

该工具用于读取本地音频元数据、按专辑整理文件夹，并生成缺失或数量异常报告。

## FLAC 转 MP3 工具

```bash
python flac_to_mp3_tool/flac_to_mp3_gui.py
```

用于批量转换本地 FLAC 文件。运行前请确认 `ffmpeg` 已安装并加入 PATH，或工具配置中能找到 ffmpeg。

## 打包 GUI

打包音乐歌单工具：

```bat
music_playlist_tool\build_gui_exe.bat
```

打包脚本会：

1. 准备 `runtime\music-api`。
2. 清理旧的 `music_playlist_tool\build` 和 `music_playlist_tool\dist\MusicPlaylistGui`。
3. 使用 `music_playlist_tool\MusicPlaylistGui.spec` 重新打包。

打包完成后输出：

```text
music_playlist_tool\dist\MusicPlaylistGui\MusicPlaylistGui.exe
```

如需让打包后的 GUI 完全脱离本机 Node 环境，需要额外放入：

```text
runtime\node\node.exe
```

否则 GUI 会优先尝试使用系统 PATH 中的 `node.exe` 启动内置 `runtime\music-api` 服务。

## 测试与代码检查

运行测试：

```bash
npm test
```

运行 lint：

```bash
npm run lint
```

Python 文件语法检查示例：

```bash
python -m py_compile music_playlist_tool\gui_app.py music_playlist_tool\auto.py music_playlist_tool\qq-auto.py
```

## Git 推送

首次关联远程仓库：

```bash
git remote add origin https://github.com/MichaelTScofield/music-api.git
```

如果远程已存在：

```bash
git remote set-url origin https://github.com/MichaelTScofield/music-api.git
```

提交并推送：

```bash
git add .
git commit -m "docs: update project README"
git push -u origin main
```

如果当前分支不是 `main`，先查看分支：

```bash
git branch --show-current
```

再按实际分支名推送：

```bash
git push -u origin <branch-name>
```

## 注意事项

- 不要提交 Cookie、登录配置、个人报告、构建目录或运行缓存。
- `.gitignore` 已忽略 `node_modules`、`dist`、`build`、`__pycache__`、`qq_music_cookie.txt` 和 QQ 登录 profile。
- `runtime/music-api` 是 GUI 打包时使用的本地服务副本，如只维护源码，可按需要决定是否提交。
- 本项目部分 API 逻辑来源于 NeteaseCloudMusicApi，使用前请遵守对应平台规则，仅用于学习和个人工具场景。

## 致谢

本项目的网易云音乐 API 部分基于 [Binaryify/NeteaseCloudMusicApi](https://github.com/Binaryify/NeteaseCloudMusicApi) 修改扩展。
