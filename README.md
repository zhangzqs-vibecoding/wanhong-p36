# 万虹 P36 模拟器

万虹 P36 学习机原固件模拟器。当前 Rust 版可在静态网页里载入 FAT32 卡镜像、显示六栏主菜单、播放开机音，以及用电脑键盘、虚拟按键和指针操作固件。

```bash
rustup target add wasm32-unknown-unknown
cargo install wasm-pack --locked --version 0.13.1
scripts/build-web.sh
python3 -m http.server 8079 --bind 127.0.0.1 --directory web
```

打开 `http://127.0.0.1:8079/`，选择已有的 `.img`，或选择含 `p36-sd-content.img` 的本地目录。页面从 FAT32 根目录读取 `sword_elfd.bin`。选择「开启声音」后播放固件音频。固件扫描卡镜像中的课程、字库和媒体目录。

网页把固件写入的 SD 扇区保存在当前站点的 IndexedDB，原镜像以只读方式使用。刷新后重新选择同一镜像即可继续。存档按文件名、大小、修改时间和文件前 256 KiB 的 SHA-256 识别。

「休闲娱乐 → MP3 音乐」读取卡里的 `休闲娱乐/mp3/`。使用 `Enter` 播放或暂停，按左右键快退或快进，再按 `Enter` 继续，`PageUp` 和 `PageDown` 切歌。文件流式播放需要支持 `audio/mpeg` Media Source 的浏览器，已用 Chromium 验证。

「休闲娱乐 → MP4」读取 `休闲娱乐/mp4/`。Chromium 已验证 MJPEG 画面和 MP3 音轨的 AVI。`Enter` 暂停或恢复，`Esc` 返回列表。

「休闲娱乐 → NES 游戏」读取 `休闲娱乐/nes游戏/`。手柄 1 用方向键移动，`Z`、`X` 对应 A、B，右 `Shift` 选择，`Enter` 开始。手柄 2 用 `WASD` 移动，`F`、`G` 对应 A、B，`Q` 选择，`E` 开始。进入游戏后可用页面上的两组虚拟按键，第一组方向键在机身区域。`Esc` 返回游戏列表。Chromium 已用自编 NROM 卡带验证两组手柄的键盘和触屏输入，并用自编 CNROM 和 UNROM 卡带验证换页。

「工具宝箱 → 录音机」需要在页面开启麦克风并授权。按空格开始或停止录音，按 `Enter` 回放，按 `F2` 或虚拟「保存」键打开保存对话框。刷新并重新加载同一镜像后，在录音机按虚拟「打开」键，选择录音，再用 `Enter` 回放。

## 目录

- `crates/p36-core` 是平台无关的 ARM 执行、SDHC/L2 DMA、GPIO、定时器、中断、LCD 和 DSP 模型。`BlockDevice` 由宿主提供扇区读取。
- `crates/p36-web` 是 wasm-bindgen 平台接口。`web/worker.js` 在 Web Worker 里推进机器并按需读取本地大文件，主线程绘制 LCD 与调度 Web Audio。
- `emu` 是 Python/Unicorn 参考实现与测试记录。`PROGRESS.md` 保存固件地址、外围设备接口和已验证交互。

```bash
cargo test --workspace --locked
cargo run --release -p p36-core --example boot -- \
  sdcard/sword_elfd.bin emu/p36-sd-content.img 1800000000 /tmp/p36-frame.rgb565
```

## 发布

GitHub Pages 选择 GitHub Actions 作为发布源后，`.github/workflows/pages.yml` 会在 `main` 更新时构建 WebAssembly、上传静态产物并部署。

`p36-sd-content.img` 是 4 GiB FAT32 镜像。可用 `scripts/pack-card.sh image.img p36-sd-content.img.gz` 打包可再分发的镜像，从 Release 下载后解压再从网页加载。发布压缩件前可用 `gzip -t` 校验完整性。固件、原机资源和个人存档由用户提供。

## Rust 版当前范围

Chromium 已验证开机画面和声音、《陈平过河》正文和朗读、《问候语》听写与学习记录恢复、词典连续输入和发音、万虹书苑翻页、电子画板拖动、三点触摸校准、刷新后恢复系统音量、文件 MP3 控制、两段 AVI 的音画同步、NES 游戏、录音保存与重新打开，以及电子琴。

虚拟键参照 P36 产品图和固件「硬件诊断 → 按键检测」排列。聚焦屏幕后可用电脑键盘输入字母、数字、标点和导航键。长按「电源」让固件关机，再点一次可从当前 SD 卡重新开机。机身上与键盘重复的导航键使用相同的键码。

「课本点读」列出 SD 卡 `课本点读/` 下的 `.lib` 文件。「同步辅导」在 `同步辅导/<科目>/` 查找 `.std` 文件。已从同步辅导的语文目录打开测试用的《益智故事》正文并播放朗读。两项的原版教材文件仍在查找。
