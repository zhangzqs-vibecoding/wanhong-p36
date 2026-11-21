# 万虹 P36 学习机 —— 逆向与模拟器进展笔记

> 最后更新：2026-11-21
> 工作目录：`/home/zzq/code/wanhong`
> 数据目录：`sdcard/`（设备 SD 卡内容）
> 代码目录：`emu/`

---

## 0. 当前进度

**原固件已从 FAT32 SD 镜像启动到六栏主菜单，浏览器可打开课程正文、播放固件语音并返回目录。**
`emu/p36-before-sd.png` 保留「文件读取错误，系统正在关机！」的基线。
`emu/boot-verified.log` 记录 SD 挂载、资源文件句柄和开机 MP3 播放完成，
`emu/p36-boot-verified.png` 和 `emu/sdram-verified.bin` 分别为主菜单截图和 32 MiB SDRAM 快照。
`emu/web-playback-verified.log` 记录固件打开 `D:/SYSFILES/益智故事.std`、生成语音以及退出正文。
`emu/p36-browser-reading-verified.png` 和 `emu/p36-browser-escape-verified.png` 分别为浏览器中的
《陈平过河》正文和按 `Esc` 返回后的故事目录。`emu/browser-scheduling-verified.log` 记录 Web Audio
收到编号连续的 448 个 PCM 块，423 块含非零样本，调度间隙均在 4 毫秒内。
`emu/p36-story-narration-verified.wav` 保存完整的 57.34 秒原始语音。

原机「休闲娱乐 → MP3 音乐」已验证播放 SD 卡曲目、暂停与恢复、快进、切换曲目和返回列表。
Chromium 测到非零输出，暂停期间播放位置保持不变且输出为零。
可在 `emu/mp3-browser-verified.log` 查阅浏览器采样及固件的文件打开和 DSP 状态日志。
`emu/p36-mp3-playing-verified.png`、`emu/p36-mp3-seek-verified.png` 和 `emu/p36-mp3-return-verified.png`
分别记录播放、快进后的画面和返回列表。
浏览器触屏输入经 GPIO0、ADC 和固件触摸回调到达菜单。`emu/p36-touch-math-verified.png`
记录进入「数学园地」后的画面，`emu/touch-browser-verified.log` 记录对应的按下、抬起和坐标。

原机「休闲娱乐 → MP4」通过 AVI（MJPEG + MP3）实测。固件打开并解封装 8 秒测试片，
宿主解码 JPEG 到固件指定的 YUV420 缓冲，LCD 扫描第一视频层。浏览器收到了 51 次视频刷新，
并收到共 48,431 字节的 MP3，SHA-256 与源文件的音轨相同，Web Audio 实测有非零输出。
`emu/video-browser-verified.log` 记录校验和、固件解码和返回过程。
`emu/p36-video-frame1-verified.png`、`emu/p36-video-frame2-verified.png` 和
`emu/p36-video-return-verified.png` 分别记录两个播放时刻和按 `Esc` 返回后的视频列表。
默认服务重启后从原镜像和现有存档打开《陈平过河》，采集到 6 块非零朗读 PCM，再用 `Esc` 返回。
见 `emu/course-video-browser-verified.log` 和 `emu/p36-course-video-regression.png`。

原镜像实测可打开「万虹书苑 → 中华国粹 → 中华武术 → 中华武术的起源与发展」，
按右方向键从第 1 页翻到第 2 页。`emu/library-browser-verified.log` 记录数据文件和按键，
截图见 `emu/p36-library-reading-verified.png` 和 `emu/p36-library-page2-verified.png`。

原机「休闲娱乐 → NES 游戏」已用自编 NROM 卡带验证画面、按键、音频和返回列表。
固件打开 `D:/休闲娱乐/nes游戏/P36TEST.NES`，由固件运行游戏逻辑。`0x20022100` 的命令 `0x21`
将 248×224 RGB24 游戏缓冲放大为 496×448 RGB565，写入 LCD 指定位置。
浏览器从固件在 `0x305ADF7A` 的键表获取方向键、A/B、Select 和 Start 映射。
按 `X` 或右方向键时，测试画面由蓝变绿，松开恢复，`Esc` 返回列表。
音频由 `0x3001DEA0` 的 APU 写入驱动，DMC 按 `0x31E18EBC` 的 PRG bank 表取样。
宿主以单调时钟推进 APU，向浏览器发送 32 kHz 单声道 PCM。实测长按 `X` 收到 33 个有声音的块，
调度时长 2.112 秒，首尾到达时间差 2.057 秒，最大调度间隙 6 毫秒。
长按区间的 64 次 Web Audio 输出采样均非零，稳定音段约 440 Hz，退出后输出为零。
见 `emu/nes-browser-verified.log`、`emu/p36-nes-idle-verified.png` 和 `emu/p36-nes-input-verified.png`。

网页 NES 模式已接入原生 Unicorn `UC_HOOK_BLOCK` 预算。每个基本块按代码字节数扣预算，
IRQ 仍按宿主单调时钟推进。退出游戏后注销原生钩子。缺少可选库时使用原指令计数路径。
同一实例用持续换色的 NROM 测试卡交替运行两种路径。稳定窗口的游戏帧率从约 10–11 提高到
30–41 帧/秒，LCD 提交从约 2.5–3 提高到 7.5–10.5 次/秒，Chromium 实测像素变化从
2.6–2.8 提高到 8.1–8.6 次/秒。测量时并行运行其他回归实例，实际速度随宿主负载变化。
固件在 `0x3003EC88` 设置默认提交间隔 4，`0x30019094` 读取配置，
每四个游戏帧经 `0x3006BD10` 提交一次画面。保留原固件设置。
同一浏览器验收 A、B、右方向键、松键静音、返回列表和重新进入。
见 `emu/nes-blocks-verified.log`、`emu/p36-nes-blocks-idle.png`、
`emu/p36-nes-blocks-pressed.png` 和 `emu/p36-nes-blocks-reopened.png`。
普通入口回归通过课程朗读、MP3 暂停恢复与切歌、AVI 画面与 MP3 音轨校验，
见 `emu/nes-blocks-regression.log`。
同一正常入口另用自编 UNROM（mapper 2）和 CNROM（mapper 3）卡带验证换页。
UNROM 测试程序先初始化 CHR RAM，再按手柄状态切换 PRG bank，绘制蓝色或绿色竖条纹。
CNROM 切换 CHR bank，在竖条纹和横条纹之间切换。两者松键后均恢复，`Esc` 返回列表。
见 `emu/nes-mappers-verified.log`、`emu/p36-unrom-switched.png` 和 `emu/p36-cnrom-switched.png`。

接入 NES 后再次验证了课程正文和朗读、书苑翻页、MP3 播放与暂停恢复。
AVI 两次复测仅见 13 和 14 次浏览器刷新，探针测到固件解码约 120 帧但仅提交 36 次 LCD 刷新。
视频播放改用与 NES 共用的宿主单调时钟及 30,000 条指令 IRQ 步长后，
同一样片在音轨结束前提交了 92 次 LCD 刷新，浏览器在整次试播中收到 77 次画面刷新。
完整的 48,431 字节 MP3 SHA-256 仍与源文件一致。证据在 `emu/avi-media-clock-verified.log`
和 `emu/p36-avi-clock-frame2-verified.png`，初次回归记录在 `emu/nes-media-regression.log`。

AVI 播放已按浏览器音频时钟呈现画面。固件 `0x300B14E8` 的播放器状态 `+0x2C/+0x34/+0x38`
提供帧末 PTS、下一帧序号和播放起始 PTS。用两次提交之间的序号差和 PTS 差换算帧起点。
MP3 批次逐帧读取样本时长，为中途开启声音的浏览器提供该批次的音轨位置。
Chromium 用时间码、画面闪标及对应的声音脉冲实测。相同 1.0–9.5 秒区间内，
帧时间减音频位置的中位数从 +359 毫秒变为 −24 毫秒，绝对误差 P90 从 393 毫秒降到 57 毫秒。
完整音轨的 60,343 字节 SHA-256 与源文件一致。画面播放到 3 秒时开启声音，
从音轨约 2.534 秒的已缓冲批次接入，后续绝对误差 P90 为 92 毫秒，按 `Esc` 回到列表并停止声音。
证据见 `emu/avi-sync-verified.log`、`emu/p36-avi-sync-verified.png` 和 `emu/p36-avi-sync-exit.png`。

首帧探针在真实视频缓冲提交前读到 LCD 仍扫描 20×20 启动画面。样片第 0 帧提交为序号 50，
固件约 5 毫秒内已推进到序号 52，网页当时首次采到第 3 帧，比音频 `playing` 晚 496 毫秒。
模拟器现在在 LCD 锁存当前音轨的 PTS 0 画面时保留像素，网页先显示该帧再播放音频。
若迟迟收不到首帧，网页在约 1.5 秒后起播，已用 0.287 秒音轨验证可自然结束。
最终样片实测第 0 帧比 `playing` 早 6 毫秒。自然播完后固件再次打开片源，重播的第 0 帧
比新音轨 `playing` 早 25 毫秒。首轮 1.0–9.5 秒的绝对误差 P90 为 51 毫秒，
完整音轨仍为 60,343 字节且 SHA-256 与源文件一致。

画面已播放到第 3 秒时开启声音，浏览器在已缓冲批次内定位到最近显示的画面时间。
开声后前 500 毫秒的音画绝对误差 P90 从 495 毫秒降到 45 毫秒，完整观察窗口为 56 毫秒。
视频点选播放后用 `Enter` 可暂停和恢复，暂停约 750 毫秒时浏览器音频位置保持在 1.492 秒。
按 `Esc` 返回列表并释放音频。见 `emu/avi-startup-verified.log`、
`emu/p36-avi-first-frame-verified.png`、`emu/p36-avi-startup-paused.png` 和 `emu/p36-avi-startup-exit.png`。
默认端口已用原镜像和现有存档重启，Chromium 验证六栏主菜单，见 `emu/p36-avi-startup-home.png`。

固件在开播时集中提交视频画面，网页定期采样曾让第 0 帧停留约 860 毫秒。
LCD 锁存时现保留每条视频音轨首秒内的画面，最多 24 张，按 PTS 去重。
持续观看的网页按提交序号取回这些画面，并按音频时钟绘制。新打开的页面从当前画面接入。
同一 15 fps 样片的首次画面变化从音频开始后 860 毫秒提前至 100 毫秒，首秒可见帧数从 3 增至 7，
音画误差 P90 从 737 降至 137 毫秒。首轮 1.0–9.5 秒区间的 P90 为 54 毫秒，音轨校验和一致。
自然重播、中途开声和 `Esc` 退出均通过，另一段 8 秒 AVI 仍完整收到 48,431 字节音轨。
见 `emu/avi-motion-verified.log` 和 `emu/p36-avi-motion-verified.png`。更新后从原镜像启动的
默认服务已回到六栏主菜单，见 `emu/p36-avi-motion-home.png`。

原机「工具宝箱 → 录音机」已用 Chromium 模拟麦克风验证录音、回放和保存。
浏览器以 8 kHz 单声道送入 PCM，原固件通过 `0x410F` 命令 `6` 和 DSP 的
`0x4802` / `0x4822` 窗口读取 AMR-NB 帧并写入 FAT32。回放时命令 `1` 驱动
原固件逐帧供数，宿主解码后复用网页的 WAV 输出。
440 Hz 测试中网页提交 159 个 PCM 块，录音停止后 API 计数保持不变，
回放采到 44 个含约 440 Hz 信号的 PCM 块。修复前固件回放 250 帧，约 5 秒，
保存为 `D:/SYSFILES/录音机/58.AMR` 后从 SQLite 扇区覆盖层读回 8,014 字节。
见 `emu/recorder-browser-verified.log`、`emu/p36-recorder-verified.amr`、
`emu/p36-recorder-capturing-verified.png` 和 `emu/p36-recorder-saved-verified.png`。
停止录音时在 `0x30051570` 设置固件的刷新标志 `0x31D53CF8`，由固件通过 `0x3004FF40`
提交录音环形缓冲的剩余数据。修复后实测停止时额外提交 1,024 字节，`63.AMR`
从独立存档读回 9,038 字节，其中 282 个 AMR-NB 帧全部由原固件回放。
本轮 DSP 总计生成 288 帧，停止时有 6 帧尚未进入固件的环形缓冲。
见 `emu/recorder-tail-verified.log`、`emu/p36-recorder-tail-verified.amr` 和
`emu/p36-recorder-tail-playback-verified.png`。

「工具宝箱 → 我的地盘 → 电子画板」已用 Chromium 验证鼠标和触屏拖动。
鼠标直接向固件发送 `0xD03C` 的按下、移动和抬起，阶段号依次为 `2`、`5`、`1`。
触屏拖动更新 ADC 坐标并维持 GPIO0 接触电平，由原固件生成移动消息。
网页每 40 毫秒合并移动采样，服务保留队列中的轨迹点并为抬起预留位置。
默认端口实测同一块画板先后画出两条连续曲线。见 `emu/pointer-drag-verified.log`
和 `emu/p36-drag-verified.png`。

「工具宝箱 → 系统管理 → 触摸屏校正」期间保留原始 `0xFFFF000A` 触摸消息，
固件将其转换为校准界面使用的 `0xD069`。在独立存档中依次点按 `(206,160)`、`(386,250)`、
`(556,350)`，界面显示「笔尖定位成功」，从 SQLite 扇区覆盖层读回新写入的
`D:/SYSFILES/adc_to_lcd.inf`。退出后触摸屏可打开系统音量。见 `emu/touch-calibration-verified.log`
和 `emu/p36-touch-calibration-verified.png`。
同一独立实例在校准后还成功打开《陈平过河》，收到了 7 块含非零样本的 16 kHz 朗读音频，
并用 `Esc` 返回故事目录。见 `emu/course-input-regression.log` 和 `emu/p36-course-input-regression.png`。

原卡「工具宝箱 → 电子词典」已验证中小学英汉词典和新华字典。浏览器以 130 毫秒间隔输入
`appla`，固件先前只处理成 `apa`。普通文本按键现等待固件调度器空闲后送入，最长等待
1.5 秒。复测收到完整的 `appla`，`Backspace` 对应原机 `0x22`，`Delete` 对应 `0x1F`。
改字后查到 `apple`、音标和「苹果」释义。点击右上喇叭时，固件提交 2,636 字节的发音 MP3，
Chromium 解码得约 0.836 秒的片段，并测到非零音频输出。
新华字典用原机拼音输入 `zhong`，按 `1` 选择「中」，显示部首相关按钮、拼音和释义。
见 `emu/dictionary-browser-verified.log`、`emu/p36-dictionary-apple-verified.png` 和
`emu/p36-dictionary-zhong-verified.png`。课程朗读与 NES 游戏按键及音频回归通过。
默认服务已用原镜像和现有存档回到六栏主菜单，见 `emu/p36-dictionary-home-verified.png`。

原卡「第二课堂 → 英语园地 → 听力训练 → 新课程 → 问候语」已在 Chromium 通关。
第一课共有 15 句、62 个单词。听写时输入字母即可，固件补齐空格和标点。
斜线分隔的词组也要全部输入，例如键入 `hellohi` 后，`Hello! / Hi!` 都会变绿，再按 `Enter` 进入下一句。
固件提交了 15 句的原机语音。首句 MP3 为 6,583 字节，源采样率 11.025 kHz，Chromium 播放约 2.194 秒，
测得输出峰值 0.843。输入全课后末句显示错误数为 0。《问候语》随后从「新课程」移到「已学课程」，
过关和最短时间均显示 `00:01:30`。

固件在退出听力训练、回到「英语园地」时才写入学习记录。Capstone 定位到退出清理
`0x300F7884` 检查记录更新标志，再调用 `0x300F774C` 写入 `D:/SYSFILES/listenrecord.lsr`。
从独立 SQLite 存档合并到 FAT32 副本读回该文件，大小由 0 增至 144 字节。
停止实例后以同一 `--sd-state` 再启动，固件重新读取记录，「已学课程」仍显示《问候语》和相同时间。
从该列表重新进入可听首句。切到「连续」，实测固件自动播放后续句子，画面推进到第 4 句，
按 `Esc` 返回已学列表。
见 `emu/listening-browser-verified.log`、`emu/p36-listening-dictation-verified.png`、
`emu/p36-listening-finished-verified.png`、`emu/p36-listening-restored-verified.png` 和
`emu/p36-listening-continuous-verified.png`。

网页屏幕下方可展开「原机快捷键」。分别测试原机矩阵键值 `0x23` 主菜单、`0x24` 课本点读、
`0x25` 同步辅导、`0x26` 第二课堂、`0x29` 万虹书苑、`0x28` 工具宝箱和 `0x2A` 休闲娱乐，
Chromium 点击后均打开对应界面。聚焦屏幕后按 `Home` 等同于原机主菜单键。
进入听力训练播放首句后按 `Home` 回到六栏菜单，顶部「返回」与 `Esc` 也通过回归。
390 像素宽的 Chromium 触控模拟可点按「工具宝箱」再返回主菜单。
见 `emu/shortcuts-browser-verified.log`、`emu/p36-shortcuts-library-verified.png`、
`emu/p36-shortcuts-home-verified.png` 和 `emu/p36-shortcuts-mobile-verified.png`。

### 启动与证据

```bash
cd /home/zzq/code/wanhong
python3 emu/web.py --sd-image emu/p36-sd-content.img
```

浏览器打开 `http://127.0.0.1:8036/`。点击「开启声音」以播放开机音、课程提示音和正文朗读。
主菜单出现后，依次进入「第二课堂 → 语文园地 → 益智故事 → 故事小屋（一） → 陈平过河」。
「返回」或 `Esc` 发送原机返回键 `0x2B`。触屏和触控笔的点按及拖动经触摸 ADC 送入固件。
点击或用 `Tab` 聚焦屏幕后，字母、数字、方向键、`PageUp`、`PageDown`、`Enter`、`Home`、
`Backspace` 和 `Delete` 通过 GPIO 矩阵送入固件。
`emu/browser-keypad-final.log` 与 `emu/web-final-verified.log` 记录 `Q`、`A`、`0` 的按下和抬起，
以及失焦时释放按键。
建镜像用 `python3 emu/make_sd_image.py emu/p36-sd-content.img`，仅在目标镜像尚不存在时执行。
按固件里 GB18030 编码的 `D:/SYSFILES/` 路径放置课程、词典及资源，保留 FAT 长文件名。
建镜像需要 `dosfstools` 和 `mtools`。运行需要 Python 和 Unicorn，JPEG 图片解码调用 FFmpeg。
MJPEG 解码与 YUV 转换需要 Pillow 和 NumPy。本机已安装这些工具，生成镜像与运行模拟器均可在普通用户权限下完成。

原机 MP3 列表扫描 `D:/休闲娱乐/mp3/`。可把待播放的 `.MP3` 放进镜像源目录的 `休闲娱乐/mp3/`，
然后构建一份新镜像。网页实测用 `Enter` 播放、暂停和恢复，`PageUp`/`PageDown` 切换曲目。
按右键进入快进，再按 `Enter` 从选定位置继续。测试使用独立镜像，曲目 `MUSIC.MP3`
取自 `sdcard/数码相册背景音.mp3`，`SHORT.MP3` 是从该曲截取的 8 秒片段。

视频列表扫描 `D:/休闲娱乐/mp4/`。已用独立 FAT32 镜像测试 320×240、15 fps 的 MJPEG
和 22.05 kHz 单声道 MP3。测试文件为 `/tmp/p36-video.HKuzTJ/TEST.AVI`，在该实验目录仍存在时可复现：

```bash
python3 emu/web.py --port 8045 --temporary --sd-image /tmp/p36-mp3.bcpoj6/card.img
```

NES 列表扫描 `D:/休闲娱乐/nes游戏/`。用 `make -C emu/nes_audio` 编译音频和可选加速库，
需要 `gcc`、`g++`、`make` 和当前 Python 的 Unicorn 包。可用
`python3 emu/nes_audio/make_test_cart.py /tmp/P36TEST.NES`
生成测试卡带，将其放到自备镜像源目录的 `休闲娱乐/nes游戏/`，再构建一份新镜像。
网页中 `Z`/`X` 对应 A/B，方向键对应手柄方向，`Shift`/`Enter` 对应 Select/Start。

录音与 AMR 回放使用 `libopencore-amrnb.so.0`。本机已将 Ubuntu 包中的库解包到
`/home/zzq/.local/share/p36/lib/`，无需系统安装。在未安装系统包时用以下命令启动：

```bash
LD_LIBRARY_PATH="$HOME/.local/share/p36/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  python3 emu/web.py --sd-image emu/p36-sd-content.img
```

进入录音机后点击「开启麦克风」并授权，按空格开始或停止录音，按 `Enter` 回放或暂停。
按 `F2` 打开固件的保存对话框，输入文件名并点击「确定」。

Web 服务默认将固件写入的 SD 扇区保存到镜像旁的 `p36-sd-content.img.state.sqlite3`，原始镜像以只读方式打开。
`--sd-state 路径` 可指定另一份存档，`--temporary` 让写入只保留在本次运行的内存中。存档需配合创建它的原始镜像。
浏览器实测在「工具宝箱 → 系统管理 → 系统音量」用左右方向键调整并按 `Enter` 保存。固件写入 `sys.dat`，
重启后读回 8 格。证据见 `emu/p36-volume-restored.png` 和 `emu/sd-state-verified.log`。

```bash
python3 emu/iter.py 1800000000 --sd-image emu/p36-sd-content.img \
  --frame emu/p36-boot-verified.png --snapshot emu/sdram-verified.bin --trace \
  > emu/boot-verified.log
python3 -m unittest discover -s emu -p 'test_*.py'
```

### 已查证的硬件映射

| 区域 | 当前结论 | 证据 |
|---|---|---|
| `0x20010000` | LCD，`+0x14` 为 RGB 扫描地址，`+0xAC` 为屏幕几何，`+0xC8` bit 11 为刷新 | 普通界面读取 `0x304DC3D8` 的 800×480 RGB565，与 `0x302A9BD8` 的 RGB888 绘图缓冲比对通过。视频读取 `+0x5C/60/64` 的三个 YUV420 平面，`+0x68/6C` 为几何，`+0x74` 为位置 |
| `0x20020000` | SDHC，`+0x34` 为状态 | `boot-verified.log` 中有 `CARD INITIALIZE OK`、`success to mount sd` 和 `fw open` 字库、资源、学习历史等文件 |
| `0x2002C000` / `0x48000000` | L2 DMA / 低地址 L2 SRAM | SD 读写经 L2 缓冲与 SDRAM 搬运。Web 服务把写入保存在镜像旁的扇区存档中 |
| `0x08000018–28` | 五路定时器，共用 IRQ 27 | 通道 2 的 `0x08000020` 经 `0x30093C14 → 0x302179C4` 投递 Nucleus tick，`[0x305B5048]` 递增 |
| `0x08000070` | 低 10 位为电池 ADC | 电量读取函数 `0x3001F454`。模拟读数 640，固件越过低电量关机 |
| `0x08000068/6C`、`0x080000BC` | 触摸 ADC、GPIO0 低电平为按下 | 固件以 `(1023−ADC_6C, ADC_68)` 生成 `0xFFFF000A` 触摸消息。`touch.py` 在普通界面将按下、移动和抬起转换为 `0xD03C` 指针消息，在校准界面保留原始消息 |
| `0x30224B90` | 11 行 × 9 列的按键矩阵 | `keypad.py` 读取固件映射为对应列 GPIO 供给电平；GPIO IRQ 经 `0x0800004C` bit 26 和共享 IRQ 27 投递。最终日志记录三个不同列的键值及抬起 |
| `0x20026000` | UART0 | `0x30066FB0` 以 115200 波特率初始化，接收窗口在 `0x48001000` |
| `0x20050020/24` | DSP IDMA 选择/数据，IRQ 5 | MP3 用 `0x4010=5` 请求供帧，`0x4003` 的值 1/0 分别暂停和恢复。文件帧在 `0x3004D394` 捕获，负载来自 `0x31D53CFC` 的 64 KiB 环形缓冲。正文 PCM 用 `0x4001=0x0E` 请求，固件用 `0x400B` 提交样本 |

镜像包含 81 个源文件，使用 32 KiB 簇。FAT32 检查和文件抽样比对通过。SD 启动提供
芯片 ID `0x33323236`，保留 FAT 回调表，并在模拟内存中将 `0x30217910`、`0x3021791C`
的缓存维护 helper 替换为返回。`0x305B5818` 的面板条目数在 SD 启动后实测为 `1`，
本轮启动日志无 `lcd invalid`。面板为 `HSD070IDW1-A00`，描述符在 `0x30226B68`。

### 当前边界

普通界面的定时器时基按指令预算近似。NES 和视频播放在网页模式中使用宿主单调时钟。
浏览器鼠标通过固件消息队列投递，触屏点按、拖动和校准已用 Chromium 模拟触屏验证。
浏览器已接入常用导航键及原机七个菜单快捷键。其余功能键仍待映射。
课程底图调用宿主 FFmpeg 完成 JPEG 解码。DSP 模型提供 mailbox 和中断，浏览器解码固件提交的 MP3 数据。
开机 MP3 源为 44.1 kHz 双声道，时长约 10.27 秒。正文朗读由固件生成 16 kHz、16 位单声道 PCM，
模拟器在 IDMA 捕获并封装为 WAV。固件轮询等待语音队列腾出空间时，模拟器提前发出 DSP 请求，
减少播放供样间隙。浏览器输入在 RTOS 调度器空闲时提交，固件忙于生成语音时可能延后响应。
系统音量由固件 `0x300374BC` 跟踪，浏览器把固件的 `9 × 档位 + 10` 归一化为音频增益，档位 0 时静音。
同一段开机音频经 Web Audio 实测在 8 格时有非零输出，保存为 0 格后输出为零。
文件 MP3 通过 MediaSource 连续解码，供帧按宿主单调时钟推进。seek 成功后清除浏览器旧数据并开始新流。
AVI 中的零长度视频包沿用上一帧。MJPEG 解码入口 `0x3011F92C` 由 `video.py` 实现。
视频播放器预填 MP3 缓冲的调用返回地址为 `0x3011F814`，后续帧复用 DSP 环形缓冲路径。
`emu/video-baseline.log` 和 `emu/p36-video-black-baseline.png` 保留修复前的黑屏记录。
当前视频显示覆盖第一 YUV 层。首秒保留固件实际提交的画面，后续画面由网页按常规采样。
RGB 和其他视频层的混合、其他片源的帧率和同步精度待进一步测量。
其他视频和音频格式待验证。

原始 SD 镜像没有 `课本点读/`、`同步辅导/`、`第二课堂/视频课堂/`、`休闲娱乐/mp4/` 和 `休闲娱乐/nes游戏/`。
其中课本点读扫描路径来自固件 `0x3010C7E8`，浏览器实测显示「无内容」，见 `emu/p36-textbook-empty-verified.png`。

### Rust / WebAssembly 复测

静态网页经「从本地目录加载」启动原 FAT32 镜像。补入固件用到的 ARM `CLZ` 和有符号半字乘加后，
Chromium 中第二课堂图标及故事列表可正常显示。依次点入「第二课堂 → 语文园地 → 益智故事 →
故事小屋（一） → 陈平过河」，待 LCD 提交正文后可见完整标题和中文段落，Web Audio 调度了多个非零
单声道 PCM 块。按 `Esc` 返回故事列表。见 `emu/p36-wasm-story-verified.png`、
`emu/p36-wasm-story-return.png` 和 `emu/wasm-story-verified.log`。

静态网页已把原固件写入的 SD 扇区保存在 IndexedDB。Chromium 在「工具宝箱 → 系统管理 →
系统音量」从 10 格降至 9 格，按 `Enter` 保存，数据库记录由 1,539 个扇区增至 1,542 个。
第二个标签页打开同一镜像时触发写入锁。刷新原标签页、重新选择原镜像并启动，
固件界面显示 9 格。校验了原镜像的大小、修改时间和 inode。见 `emu/wasm-sd-state-verified.log`
和 `emu/p36-wasm-volume-restored.png`。

Rust 网页版已用带 `MUSIC.MP3` 和 `SHORT.MP3` 的独立 FAT32 镜像验证「休闲娱乐 → MP3 音乐」。
固件从 `0x3004D394` 提交帧，Rust 从 64 KiB 环形缓冲重组 MP3，按宿主单调时钟发 DSP 请求，
网页通过 MediaSource 解码。暂停 1.2 秒期间播放位置保持在 0.659 秒，浏览器采样降至零，
恢复后继续有声。右键快进再按 `Enter` 后，捕获数据在原文件中的偏移从 1,920 跳到 705,763。
上下曲切换、`Esc` 停音及播放中开启声音均实测通过。

`SHORT.MP3` 的探测头比实际首帧多一个填充字节。Rust 使用环形缓冲内的首帧头修正长度。
浏览器自然播完 8.046 秒并回到曲目列表，捕获的 128,731 字节与原文件偏移 590 至末尾逐字节一致。
证据见 `emu/wasm-mp3-verified.log`、`emu/p36-wasm-mp3-playing.png` 和
`emu/p36-wasm-mp3-ended.png`。开机 MP3 和《陈平过河》朗读回归见
`emu/wasm-mp3-course-regression.log`。

Rust 网页版已用两段 MJPEG + MP3 AVI 验证「休闲娱乐 → MP4」。固件从 `0x3011F92C` 调用
宿主 JPEG 解码并获得 YUV420，从 `0x300B14E8` 提取帧时间戳。LCD 锁存第一视频层并保留首秒画面，
网页以 MediaSource 的音频位置调度视频。视频播放时用宿主单调时钟推进定时器。

320×240、15 fps 的 10 秒时间码片在 Chromium 中从第 0 帧开始，首帧采样比 `playing` 晚
13 毫秒。首秒显示 12 张不同画面，全段观察到 147 张。1.0–9.5 秒的音画误差绝对值 P90
为 65 毫秒。自然重播后暂停约 750 毫秒，画面与音频位置保持不变，恢复后按 `Esc` 退出。
静音播放约 3 秒再开启声音，采样窗口的音画误差绝对值 P90 为 69 毫秒。另一段 8 秒片的
视频提交有 113 个不同时间戳。两段音轨分别为 60,343 和 48,431 字节，均与源文件逐字节一致。
见 `emu/wasm-avi-sync-verified.log`、`emu/wasm-avi-late-audio-verified.log`、
`emu/wasm-avi-second-verified.log` 和 `emu/p36-wasm-avi-playing.png`。
普通 MP3 控制及原镜像课程回归见 `emu/wasm-avi-mp3-regression.log` 和
`emu/wasm-avi-course-regression.log`。

Rust 网页版已通过自编 NROM 卡带的画面、按键和声音验证。固件向 `0x20022100` 写命令 `0x21`，
模拟器将 248×224 RGB24 双线性放大为 496×448 RGB565，按固件填写的步幅和偏移写到 LCD 缓冲。
最初转换后显存全零，独立 Rust 探针从 `0x31BA3710` 读到了非零源像素。修正了插值累积值的
移位并用单测验证颜色与目标偏移。

网页读取固件的手柄表 `03 0B 08 00 01 09 02 0A`。Chromium 按住 `X`、`Z` 或右方向键时
测试画面由蓝变绿，松开恢复。固件在 `0x3001DEA0` 的 APU 写入由 TetaNES APU 合成为
32 kHz 单声道 PCM。长按 `X` 时调度的 26 个音频块均为约 440 Hz，合计 1.664 秒。
`Esc` 返回卡带列表，浏览器输出峰值回到零，从列表重新打开后恢复蓝色画面。
见 `emu/wasm-nes-verified.log`、`emu/p36-wasm-nes-idle.png`、
`emu/p36-wasm-nes-pressed.png` 和 `emu/p36-wasm-nes-returned.png`。
同一入口用自编 CNROM 和 UNROM 卡带复测。CNROM 改变 CHR bank，将竖条切成横条。
UNROM 改变 PRG bank，把蓝色竖条切成绿色。两者松开按键后恢复，按 `Esc` 回到列表。
见 `emu/wasm-nes-mappers-verified.log`、`emu/p36-wasm-cnrom-switched.png` 和
`emu/p36-wasm-unrom-switched.png`。

在 390 像素宽的 Chromium 触屏模式下，同一 NROM 卡带的机身右方向键由普通导航码 `0x1D`
切换到手柄码 `0x0A`，实测画面由蓝变绿。触屏与电脑键盘同时按住同一键码时，最后一个来源抬起才松键。
虚拟 B 产生 5 个非零音频块，浏览器取消触摸后恢复静音。虚拟 A、选择和开始的按下和抬起键码均已核对。
按 `Esc` 返回后方向键恢复为 `0x1D`。见 `emu/wasm-nes-touch-baseline.log`、
`emu/wasm-nes-touch-verified.log` 和 `emu/p36-wasm-nes-touch-b.png`。

固件 `0x305ADF7A` 的后八个键码 `07 0F 0C 04 05 0D 06 0E` 对应第二手柄。两份 NROM 探针分别读取
`$4016` 和 `$4017`，按读到的位绘制八种颜色。两份卡带的颜色顺序一致，确认了 A、B、选择、开始、
上、下、左、右的顺序。原固件在 `0x30069D98` 处理写入 `$4016`，下降沿只清零 `0x31DBB3F0` 的第一手柄
游标。持续按住第二手柄时，Python/Unicorn 探针仍观察到卡带反复关闭 APU 音道。Rust 现在在同一个下降沿
也清零 `0x31DBB3F8` 的第二手柄游标，固件继续处理其余逻辑。Chromium 用同一测试镜像复测，第二手柄
持续按住 A 和右方向时各 16 个画面采样全部为按下色，松手恢复空闲色。

390 像素宽的 Chromium 触屏模式对两组手柄各八个键分别执行电脑按键和虚拟按键操作，卡带实测均收到正确位。
第二手柄 A 同时由触屏和键盘按住时，取消触摸后八个画面采样仍为按下色，键盘抬起后恢复。
`Esc` 返回卡带列表，`W` 恢复为原机键码 `0x57`。可用 `emu/nes_audio/make_test_cart.py` 的
`--controller 2` 生成检查第二手柄长按的卡带。见 `emu/wasm-nes-two-ports-verified.log`、
`emu/wasm-nes-port-two-hold-baseline.log` 和 `emu/p36-wasm-nes-port-two-touch.png`。

Rust 网页版从浏览器麦克风接收 8 kHz 单声道 PCM，再通过 DSP 邮箱向固件提供 AMR-NB 帧。
Chromium 用 440 Hz 模拟信号录音，网页提交 135 个 PCM 块，停止后计数不再增加。固件保存
`64.AMR` 后回放，浏览器收到至少 25 个有声块。从 IndexedDB 的扇区覆盖层重建卡镜像，
读回 8,654 字节，其中有 270 个完整 AMR-NB 帧，合计 5.4 秒。`ffprobe` 识别为 8 kHz
单声道。见 `emu/wasm-recorder-verified.log`、`emu/p36-wasm-recorder-saved.png` 和
`emu/p36-wasm-recorder-test.amr`。
刷新后重新加载原镜像，在录音机按虚拟「打开」，选中 `64.amr` 后回放。Chromium
收到 28 个 8 kHz PCM 块，麦克风提交计数为 0。见 `emu/wasm-recorder-restored.log`
和 `emu/p36-wasm-recorder-restored.png`。

Chromium 在原机电子琴中短按和长按 `A`，分别收到约 0.232 秒和 2.159 秒的双声道 PCM。
按住 `S` 收到约 1.161 秒的 PCM，松键后分析器测得静音。见 `emu/wasm-piano-verified.log`
和 `emu/p36-wasm-piano.png`。

Rust 网页版的词典以 130 毫秒间隔输入 `appla` 时，固件先前只留下 `apl`。Worker 现等待
RTOS 调度器空闲后递送文本键按下事件，最长等待 1.5 秒。复测输入框显示完整的 `appla`，
用 `Backspace` 和 `E` 改成 `apple` 后看到「苹果」释义。点原机喇叭按钮收到 2,636 字节
MP3，Chromium 解码约 0.836 秒。见 `emu/wasm-dictionary-verified.log`、
`emu/p36-wasm-dictionary-appla.png` 和 `emu/p36-wasm-dictionary-apple.png`。

Chromium 通过静态网页版完成听力训练《问候语》的 15 句听写，末句显示错误数为 0。
退出听力训练后，IndexedDB 中的扇区 `2149184` 含本课记录，过关和最短时长均为 63.2 秒。
刷新页面并重新加载原镜像后，从「已学课程」重新打开《问候语》，切到「连续」可自动播放，
截图记录进行到第 4 句。见 `emu/wasm-listening-verified.log`、
`emu/p36-wasm-listening-finished.png`、`emu/p36-wasm-listening-restored.png`
和 `emu/p36-wasm-listening-continuous.png`。

静态网页版在 Chromium 的触屏模拟中实测了电子画板拖动和三点校准。画板留下连续笔迹，
校准界面显示「笔尖定位成功」。见 `emu/p36-wasm-touch-drag.png` 和 `emu/p36-wasm-touch-calibrated.png`。

万虹书苑可打开《中华武术的起源与发展》，实测 `PageDown` 翻到第 2 页，见
`emu/p36-wasm-library-page2.png`。原固件「系统管理 → 硬件诊断 → 按键检测」显示
86 个按键的示意图。已在 Chromium 观察到键盘右方向键对应的键帽变红，
见 `emu/p36-wasm-keyboard-diagnostics.png`。
逐键截图确认 `0x12` 背景音、`0x13/0x14` 音量加减、`0x15` 输入法、`0x16` 新建、
`0x17` 帮助、`0x18/0x19` 键盘 PgUp/PgDn、`0x1E` 保存和 `0x3F` 打开。
网页键盘按诊断图补齐上述按键、标点及 Tab、Caps Lock、Shift。
Chromium 分别核对了键盘和虚拟按钮送入的键码。390 像素宽时页面保持在视口内，键盘可单独横向滚动。
见 `emu/wasm-keyboard-verified.log`、`emu/p36-wasm-keyboard-desktop.png`
和 `emu/p36-wasm-keyboard-mobile.png`。

按固件 GPIO 矩阵与独立电源描述符核对，87 个网页机身按钮覆盖 78 个独立机身键码，另核对 64 个电脑键盘映射和失焦释放。
覆盖探针在浏览器到 Worker 的消息边界核对按下与抬起。实机重复键的电气连接仍待实物核对。
见 `emu/wasm-input-code-coverage.log`。

找到[太平洋产品库的 P36 产品图](https://product.pconline.com.cn/pdlib/345699_picture.html)。
桌面网页按图将六个快捷键放到屏幕左侧，翻页和音量键放到右侧，方向键及菜单、返回、确定放到屏幕下缘。
窄屏将侧键移到屏幕下方。固件键名表 `0x3029E0AC` 确认 `0x00–0x0F` 是两组游戏手柄键，
`0x10/0x11` 是右键、左键。网页机身方向、翻页及菜单、返回、确定复用已验证的普通导航键码，
这些重复键在实机上的电气连接仍待实物核对。

固件按键描述符 `0x30224B90` 的 `+0x1C/+0x20/+0x24` 为 `10/0x40/1`。
初始化函数在 `0x3000AC60–0x3000AC80` 将 GPIO10 设为输入并注册中断。
长按电源经原固件进入关机流程，在 `0x3003F138–0x3003F13C` 将 GPIO105 拉低。
Rust 在进入后续延时循环 `0x3003F140` 时停止执行，Worker 先保存剩余扇区，再等待电源键重新启动同一张卡。
Chromium 将系统音量从 10 格改到 9 格，保存、长按电源关机、再点电源开机后仍显示 9 格，
关机时第二个标签页仍受镜像写入锁保护。见 `emu/wasm-power-cycle-verified.log`、
`emu/p36-wasm-power-shutdown.png`、`emu/p36-wasm-power-rebooted.png` 和
`emu/p36-wasm-power-volume-restored.png`。新布局见 `emu/wasm-case-layout-verified.log`、
`emu/p36-wasm-case-layout-desktop.png` 和 `emu/p36-wasm-case-layout-mobile.png`。

以本地 HTTP 服务挂载 `/wanhong-p36/` 验证了 Pages 风格的子路径。Chromium 从本地目录选择
SD 镜像，加载 WASM 和麦克风工作线程，完成录音和回放。见 `emu/wasm-pages-subpath-verified.log`。
打包脚本输出 918,727,503 字节的 gzip，完整性检查通过，解压流与原镜像逐字节一致。
Chromium 从解压产物所在本地目录启动到主菜单，收到 800×480 帧且无页面错误。
见 `emu/wasm-packaged-card-verified.log`。

补查原机课件时找到[旧商家目录](https://shop.11665.com/list0/50018004/50008870/n_16540906871.html)
曾列出兼容 P36 的 8 GB 卡。对应详情页 `n_16182667783.html` 请求超时，卡内目录和可下载性待核实。
中国文化传播网的 [P36/V39 分类](https://web.archive.org/web/20090718202510/http://wt.cnwhcb.com/download.asp?BigClassID=20)
与[七年级英语目录](https://web.archive.org/web/20090723225308/http://wt.cnwhcb.com/Download3.asp?ThreeClassID=1786)
仍可从历史快照读取。目录列有新目标英语七年级下册（ID `67092`）和北京教育版英语第 14 册辅导
（ID `53307`）。抽查多个 `tongji.asp` 下载入口以及 `www`、`gz`、`wt` 域名下的同 ID
`downurl.asp`，未取得课件。进一步交叉检索 226 个 P36/V39 目录条目，在 `www` 的历史快照找到
[ID 66884 的跳转](https://web.archive.org/web/20090721040303id_/http://www.cnwhcb.com:80/tongji.asp?id=66884)。
它直接指向 `61.142.250.36` 上的《湖北教育版初中语文七年级下册》同步辅导文件，文件名为
`湖北教育版初中语文七年级下册_48M_06F5.std`。Wayback 对 FTP 目标返回 404，CDX 无文件快照，
直接尝试 FTP 也未取得文件。

在独立 FAT32 镜像里，把原卡的 `益智故事.std` 放入 `课本点读/` 和 `同步辅导/语文/`。
Chromium 实测「同步辅导 → 语文」列出该文件，并能进入故事小屋、打开《陈平过河》正文。
进入正文后的首轮采样收到 27 个单声道音频块，其中 25 个非零。见
`emu/p36-wasm-coaching-std-list.png` 和 `emu/p36-wasm-coaching-std-story.png`。

「课本点读」在 `0x3010C6B4` 和 `0x3010CFEC` 引用 `*.lib`。同一目录放有 `.std` 和一个
20 字节的 `P36PROBE.lib` 时，原固件只列出后者，验证了文件扩展名筛选。见
`emu/p36-wasm-textbook-lib-probe.png`。该文件只用于验证列表，点读正文仍待真实 `.lib` 课件验证。
这些测试使用从无 `sdcard/` 的源码副本构建出的 WASM，在 `/web/` 子路径加载后输出
800×480 画面。见 `emu/wasm-course-paths-verified.log`。
同一源码副本的 6 项 workspace 测试通过。Chromium 通过「从本地目录加载」选择解压后的
`p36-sd-content.img`，到达六栏主菜单，页面异常和非 GET 请求均为 0。
见 `emu/p36-wasm-clean-directory-boot.png` 与 `emu/wasm-clean-directory-boot-verified.log`。
接入 NES 触屏按键后，从重新构建的静态站点和同一份本地镜像实测 800×480 开机画面、
约 10.27 秒双声道开机音、《陈平过河》正文及非零朗读 PCM。按 `Esc` 后等待故事目录实际出现，
浏览器全程无页面异常。见 `emu/wasm-clean-story-regression.log`、
`emu/p36-wasm-clean-story-body.png` 和 `emu/p36-wasm-clean-story-return.png`。
双手柄修复后的静态 ZIP 解包后也从子路径重复通过上述课程回归，并检查浏览器没有发出非 GET 请求。
见 `emu/wasm-web-package-verified.log`。

另外查到 2011–2012 年的 `down.szwanhong.com` 教材目录和 `www.szwanhong.com` 下载页存档。
已成功读取的目录主要列出其他机型和名师视频。官网下载子站的 61 条 `tongji.asp` 跳转快照
与已提取的 226 个 P36/V39 教材 ID 无交集。中国文化传播网和旧 FTP 的 Wayback CDX 查询
均未返回 `.lib` 文件快照。[2009 年的 P36 论坛讨论](https://web.archive.org/web/20240731031047/https://ww123.net/viewthread.php?tid=4676555)
未提供教材附件地址。
迅维网的两个 P36-D 帖子提到 `V5.0-0411.bin`，其中发帖人报告附件上传失败。
贴吧的系统文件汇总帖请求返回 HTTP 403，搜索结果未提供可核验的 P36 教材文件。

以下第 1–10 节保留初始探索记录，当前地址和复现命令以本节为准。

---

## 1. 设备与固件身份（已确认）

| 项目 | 结论 | 证据 |
|---|---|---|
| 设备 | 万虹 P36 学习机（约 2009 年） | 产品资料 + SD 内容 |
| CPU | **ARM925（ARM9TDMI，ARMv4T）**，小端 | 固件字符串 `Copyright MGC 2002 - Nucleus PLUS - ARM925 Code Composer v. 1.14.1` |
| RTOS | **Nucleus PLUS** | 同上 |
| 固件版本 | **Sword V2.0.3** | 字符串 `#0Sword V2.0.3` |
| 主镜像 | `sdcard/sword_elfd.bin`，2,791,656 B (0x2A98E8)，**无 ELF 头的裸 ARM 镜像** | 首字 `0xEA000006` = `B 0x30000020`（ARM 异常向量表） |
| 载入基址 | **0x30000000** | 向量表 + 所有字面量指针（`0x30xxxxxx`） |
| RAM | **32 MB SDRAM，0x30000000–0x32000000** | 各模式栈顶字面量 `0x31FEF7F8` / `0x31FDD7E8` 等 |
| MMU | 固件**会开启** MMU | `MCR p15,0,r0,c1,c0,0` @ **0x30217848**（前一条 `ORR r0,r0,#1`） |
| Cache/控制 | CP15 c1 一组 helper 函数 @0x302177E0–0x3021785C | 反汇编 |
| 系统调用 | `svc #0xab`（Thumb），**SWI handler @0x30000100 只是缓存维护 trampoline** | `0x3021531A` 处 `svc #0xab` |
| 异常向量 | 向量表在 `0x30000000`；因是相对跳转，**必须放在 0x30000000 才对**（见"关键坑"） | 反汇编 |

### 1.1 固件里能看到的能力
- 图形库 `Graghic SourceCode V2.10.5`（JPEG/MJPEG/WBMP）
- 音频 `Audio Version V2.5.17`，MP3 / WMA / AAC / AMR / WAV 解码器
- 视频/容器：AVI、H.263、MPEG、WMV demuxer 字符串（`AKV Demuxer`）
- 存储：`FSLib - NandMtd_Init()`（NAND + FAT）、`MMC1..4`
- 输入：GPIO 按键矩阵扫描、触摸 ADC、`keypad_intr_scan`
- 动态字库：`DynamicFont_*`、HZK 点阵字库

> 重要：**内容解码器都在固件里**。所以只要硬件模拟对，内容就能被解出来。

---

## 2. 硬件地址图（运行期实测得出）

| 地址 | 用途 | 依据 |
|---|---|---|
| `0x08000000` | **系统/中断控制器 + DMA/"引擎"** | IRQ handler 读的字面量 |
| `0x080000CC` | 中断 **pending** | IRQ handler @0x30217618 |
| `0x08000034` | 中断 **enable/mask** | IRQ handler + 使能函数 @0x30022600 |
| `0x08000004` | **busy/status**（bit12、bit14），被轮询 1100 万次 | 轮询循环 @0x300204A8 |
| `0x0800007C/84/9C/A0` | DMA 类：地址/长度寄存器 | 写值含 `0x5F98000`、`0x60000` |
| `0x20010000` | 未知（19 个寄存器在用，疑 **存储/MMC**） | 访问追踪 |
| `0x20020000` | 未知；`+0x34` 是**就绪/状态**，被轮询 1780 万次 | 访问追踪 |
| `0x20026000` | 未知（`+0x00`、`+0x04` 常用） | 访问追踪 |
| `0x2002C000` | **LCD 控制器** | 见 §3 |
| `0x2002D000` | **定时器 / RTC**（`+0x04`、`+0x08`、`+0x14`） | 驱动 @0x300664F0（含 /1000 换算） |
| `0x48000000` | **DSP / IDMA（音频）** | 驱动 + 字符串 |

辅助函数：
- 延时 `0x300261B0`：校准式忙等，`0x30020564` 从 `0x08000004` 的位推出"每毫秒循环数"。
- IRQ 分发 `0x300226D8`：bit → handler 查表（表项 12 字节）。
- 中断使能 `0x300225F0`。

---

## 3. 显示子系统（关键，已确认分辨率）

### 3.1 LCD 寄存器（基址 `0x2002C000`）
| 偏移 | 含义 | 观察值 |
|---|---|---|
| `+0x80` | 使能 | `0x1` |
| `+0x84` | 层基址 / 控制；**bit29 每帧翻转 = 换页** | 初值 `0x30000000`，运行中在 `0x0` / `0x20000000` 间翻转 ~3000+ 次 |
| `+0x88` | 颜色键 | `0xFFFF00FF` |
| `+0x8C` | 配置 | `0xF0FF0000` → `0x20000` |

驱动位置：`0x300916DC`（初始化那 4 个寄存器）、`0x300673A0`（层设置/换页）。

### 3.2 分辨率 = **800 × 480**（已确认！）
`Fwl_GetLcdWidth()` @`0x30017CC0` 的逻辑：
```asm
ldrb r1,[r2,#0x3c]      ; 面板表条目数
cmp  r1,r0
addhi r0,r2,r0,lsl#1
ldrshhi r0,[r0,#0x3e]   ; 返回宽度
...                      ; 越界 -> 打印 "Fwl_GetLcdWidth lcd invalid"，返回 0
```
全局指针在字面量 `0x300174F0`。**在模拟结束的内存里读到**：

- `ptr = 0x305B57DC`
- `[ptr+0x3C]`（条目数） = **0**  ← 面板表是空的！
- `[ptr+0x40]` = **0x0320 = 800**
- `[ptr+0x42]` = **0x01E0 = 480**

→ **屏幕 800×480，RGB565**。但面板表空 ⇒ `Fwl_GetLcdWidth()` 返回 0 ⇒
显示子系统"lcd invalid"。

### 3.3 画面确实画出来了
- UI 缓冲/帧缓冲观测在 **`0x30280000`**，按 **800×480 RGB565** 渲染出来，
  用户确认能看到**「文件读取错误」**字样（偏暗、偏模糊）。
- 这句报错 → **印证是"读文件失败"**，即存储没模拟。

---

## 4. 模拟器实现（`emu/wemu.py`、`emu/iter.py`）

引擎：**Unicorn 2.1.4（ARM）** + 自建设备模型。四个关键技巧：

1. **绕过 MMU**：把 `0x30217848` 那条 `MCR p15,0,r0,c1,c0,0` 改成 NOP
   （`0xE1A00000`）。固件设置 TTB/domain 后本来想开 MMU，Unicorn 会 prefetch abort
   (`INTR 3`)。关掉后跑 identity 物理地址，一切正常。
2. **低地址向量跳板**：ARM925 的异常向量必须在 `0x0`（或 `0xFFFF0000`），而固件的向量
   是**相对跳转**、只有放在 `0x30000000` 才指向正确 handler。所以在 `0x0` 放一张：
   ```asm
   ; 8 个向量都用 ldr pc,[pc,#0x18]
   0x00: E59FF018 ... 0x1C: E59FF018
   0x20: 0x30000020   ; reset
   0x24: 0x302176C0   ; undef
   0x28: 0x30000100   ; SWI
   0x2C: 0x302176FC   ; prefetch abort
   0x30: 0x30217738   ; data abort
   0x34: 0x30000020   ; reserved
   0x38: 0x30217618   ; IRQ
   0x3C: 0x3021766C   ; FIQ
   ```
3. **吞掉 SVC**：Unicorn 默认对 SVC 抛 `UC_ERR_EXCEPTION`（不自动向量）。
   用 `UC_HOOK_INTR` 忽略即可——因为这个固件的 SWI handler 本来就是空 trampoline。
4. **MMIO 寄存器文件**：
   - 写入值保存在字典里，**读回写过的值**（让驱动的配置回读成立）；
   - `0x08000004`、`0x0800000C`、`0x0800004C` 视为 **self-clear busy**，写即完成；
   - `0x20020034` **强制返回 `0xFFFFFFFF`（就绪）**，否则死等 1780 万次。
   - 已知外设窗口要**预 map**（`0x08000000/0x20000000/0x48000000`），
     否则第一次 MMIO 写会 `UC_ERR_WRITE_UNMAPPED`。

### 4.1 实测结果
- 连续跑 **3 亿条指令不卡死**；
- RTOS 已在**多任务切换**（SP 在 `0x31D26xxx` 各任务栈间变化）；
- LCD 换页位翻转 ~3000–3700 次；
- 界面出现 **「文件读取错误」**。

---

## 5. 未解决 / 下一步（按优先级）

1. **存储（最关键）** ⭐
   - 需要实现 NAND 控制器（FSLib/NandMtd）**或** SD/MMC 控制器 + FAT。
   - 现成素材：`sdcard/` 里的 81 个文件（中文名），可以打包成 FAT16/32 镜像。
   - 一旦能读文件，`文件读取错误` 应该消失，UI 才会真正加载字库/资源。
2. **LCD 面板表**
   - 让 `0x305B57DC+0x3C` 的条目数非 0，或直接给 `Fwl_GetLcdWidth` 一个合理返回
     （800×480 已知）。也可找出"谁给这张表赋值"（面板 ID 探测），把探测值喂对。
3. **输入**：GPIO 按键矩阵（扫描/中断）+ 触摸 ADC（`0x20026000` 疑与 ADC/按键有关）。
4. **定时器中断**：目前靠忙等延时能跑，但没有 tick；实现 `0x2002D000` 定时器 +
   `0x08000034`/`0x080000CC` 中断投递会更真实。
5. **音频**：`0x48000000` DSP/IDMA（可选）。
6. 渲染保真：把 `0x30280000` 的 800×480 RGB565 画面调对比度/找准确基址，
   让「文件读取错误」清晰可读（当前偏暗）。

---

## 6. 环境 / 工具（本机）

- WSL2 Ubuntu 24.04，x86_64，16 核，11 GB RAM
- **无 sudo 密码**（`sudo` 不可用）→ 不能 `apt install qemu-system-arm`
- 可用：`docker`（用户在 docker 组）、`gcc/make/cmake/git`、`objdump`（**无 ARM target**）
- 已 `pip --user --break-system-packages` 安装：**capstone 5.0.7、unicorn 2.1.4、keystone 0.9.2、pyelftools**
- **REA（Ghidra/Hopper/IDA/Kuna）全部未配置** → 没有反编译器可用，只能 capstone 手撸
- 无 PIL，PNG 用 `zlib` 手写编码

---

## 7. 复现命令

```bash
cd /home/zzq/code/wanhong

python3 emu/wemu.py 150000000     # 跑 1.5 亿条指令（基础版）
python3 emu/iter.py 300000000     # 自动满足轮询寄存器的探索版（能跑到 UI）
python3 emu/scan_fb.py 150000000  # 跑完扫描 32MB SDRAM，渲染"像图"的块
python3 emu/find_fb.py            # 按双峰度搜帧缓冲基址/格式
python3 emu/sharp.py              # 按边缘锐利度搜几何
python3 emu/ascii_fb.py           # 把候选区打成 ASCII 密度图
```

产物：`emu/sdram_full.bin`（32MB SDRAM 快照，**最重要的中间产物**）、
`emu/sdram.bin`、`emu/blk_*.png`、`emu/sharp*.png`、`emu/best*.png`、`emu/*.log`。

---

## 8. 关键坑（别重复踩）

1. **不要相信"盖住 MMU 就能跑"就完事**——MMU 是必要的，但向量基址是第二个坑。
2. **向量不能简单 memcpy 到 0**：那是相对跳转，会跳到 `0x100` 而不是 `0x30000100`。
   必须用跳板表（§4.2）。
3. **Unicorn 对 SVC 不自动向量**，不挂 `UC_HOOK_INTR` 会直接 `UC_ERR_EXCEPTION`。
4. **MMIO 页要预 map**，否则首写 `UC_ERR_WRITE_UNMAPPED`。
5. **不要"所有 MMIO 读都返回 0"**：会让驱动 ID/配置回读全错。要"写回读 + 定点 self-clear"。
6. **反汇编线性扫描会因字面量池失步**，扫整图找不到指令时用 `md.skipdata=True`，
   或按已知函数入口分段反汇编。
7. **字符串引用是 `ADD Rd,pc,#imm` 形式**，按 4 字节字面量搜不到。
   用编码模式 `(w & 0xFFFFF000) == 0xE28F0000` 扫描并算 `addr+8+imm`。
8. **`#64,#26` 这种是 ARM 旋转立即数**：`#64,#26` = `0x40 ROR 26` = `0x1000`（bit12），
   `#64,#24` = `0x40 ROR 24` = `0x4000`（bit14）。别当成移位次数。
9. **`0x30280000` 那种"像字的花屏"可能真是 UI（偏暗）**，也可能是数据噪声——
   用"双峰度"判断会被全零 BSS 带偏，要看是否有版面结构。
10. 轮次边界有时会吞掉工具调用：**写完文件要 `ls` 复核**。

---

## 9. SD 卡数据格式清单（内容侧）

| 文件 | 大小 | 魔法 | 说明 |
|---|---|---|---|
| `sword_elfd.bin` | 2.79 MB | （裸 ARM） | 主固件 |
| `万虹书苑.dat` | 355.8 MB | `BK vanhon Co.Ltd` | 书库容器；索引 **30476** 条偏移（单调、均在文件内） |
| `*.std`（38 个） | 220 MB | `WH STD.` | 私有课程包（三字经/弟子规/三十六计…），载荷高熵（压缩/加密） |
| `*.dic`（11 个） | 197 MB | `VanHong Dict` | 词典（唐诗宋词/中小学英汉/动漫/新华字典…） |
| `AkResData.Bin` | 192.9 MB | — | 资源块，**内含明文 PNG(20×20 IHDR)/JPEG(JFIF)/RIFF WAVE** |
| `ChineseField.van` | 95.2 MB | 高熵 | 疑中文字形/手写数据 |
| `Resource.irf` | 4.77 MB | `IsResV4` | 资源归档，头部声明 58 条；条目 = 16B GUID + 2×u32 |
| `Font16/24/32.hzk` | 12.3 MB | — | HZK 点阵汉字库 |
| `DynamicFont4_12/16.bin` | 3.8 MB | — | 动态字库 |
| `LangCodepage.bin` | 476 KB | `GLS_BINARY` | 代码页 |
| `ZBN.bin` | 1.18 MB | `WHFD` | 索引/字表 |
| `HRD.BIN` | 19.6 KB | `WHHR` | 硬件/按键映射 |
| `ListenTrain.lst` | 0.9 MB | `WH LST` | 听力训练 |
| `Record.rec` | 232 B | `WH03` | 录音索引（用户态） |
| `studyhistory.dat` | 1.2 KB | `StudyHistory ID` | 学习历史（用户态） |
| `单词记忆.bin/.cfg` | — | `WH STD` / `WHRW` | 单词记忆（用户态） |
| `adc_to_lcd.inf` | 3.9 KB | — | 触摸校准表 |
| `数码相册背景音.mp3` | 2.0 MB | ID3v2.3 | 标准 MP3 |

> 用户数据都很小且是默认值；8 个用户目录（我的日记/录音机/通讯录/…）**全空**
> → 这张卡基本是出厂/未使用状态。

---

## 10. 备选路线（若模拟器路线受阻）

- **A. 全系统模拟**（当前路线）：补存储/LCD面板/输入 → 真能用。
- **B. 把固件当解码器**：在 Unicorn 里直接调用固件内部的解压/解码函数，
  把 `.std`/`.dic`/`万虹书苑.dat` 内容解出来导出。见效快，适合先拿内容。
- **C. 换 OS**：WinCE 5.0/6.0 硬件规格最匹配（ARMv4T + 32MB），但仍需自写 BSP，
  且**换 OS 后学习机功能就没了**（应用/内容绑死在原固件）。最新 Linux 发行版不可能。
