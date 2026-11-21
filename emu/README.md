# 万虹 P36 学习机 —— 逆向与模拟器（进行中）

本目录是 `sdcard/` 那份 SD 卡数据对应设备的逆向工程与模拟器实验代码。

## 一、已确认的硬件/软件事实（均有证据）

| 项目 | 结论 | 证据 |
|---|---|---|
| CPU | **ARM925 (ARM9TDMI, ARMv4T)**，小端 | 固件内字符串 `Copyright MGC 2002 - Nucleus PLUS - ARM925 Code Composer` |
| RTOS | **Nucleus PLUS** | 同上 |
| 固件版本 | **Sword V2.0.3** | 固件字符串 `#0Sword V2.0.3` |
| 载入基址 | **0x30000000**（异常向量表就在文件开头） | 首字 `0xEA000006` = `B 0x30000020`；池中函数指针均为 `0x30xxxxxx` |
| RAM | 32 MB SDRAM `0x30000000`–`0x32000000` | 各模式栈顶 `0x31fe…`/`0x31fd…` 字面量 |
| MMU | 固件**会开启 MMU**（`mcr p15,0,r0,c1,c0,0` @`0x30217848`） | 该指令执行后 Unicorn 报 prefetch abort (INTR 3) |
| 异常 | SVC 用于 RTOS 系统调用（`svc #0xab`），SWI 处理体是缓存维护 trampoline | Thumb 代码 `svc #0xab` @`0x3021531a`；handler @`0x30000100` |
| 中断控制器 | **在 `0x08000000` 块内**：pending = `0x080000CC`，enable = `0x08000034` | IRQ handler `0x30217618` 读这两个字面量 |
| 外设块 | `0x08000000` 系统/中断/DMA；`0x20010000`；`0x20020000`；`0x20026000`；`0x2002C000` **LCD**；`0x2002D000` 定时器/RTC；`0x48000000` **DSP/IDMA** | 运行期 MMIO 访问追踪 |
| LCD 寄存器 | `+0x80` 使能、`+0x84` 层基址（初值 `0x30000000`，bit29 翻转=换页）、`+0x88` 颜色键、`+0x8C` | 驱动 `0x300916DC`、`0x300673A0` |
| 存储 | NAND（FSLib/NandMtd + FAT）、MMC1–4、SD | 固件字符串 `FSLib - NandMtd_Init()`、`0MMC1..4` |
| 其它 | 按键(GPIO 扫描)、触摸(ADC)、音频(MP3/WMA/MJPEG, IDMA)、动态字库 | 固件字符串 |

固件里还带：图形库 `Graghic SourceCode V2.10.5`（JPEG/MJPEG/WBMP）、音频 `V2.5.17`、以及大量
`AKV Demuxer / AVI / MP3 / AAC / WMA` 解码器字符串 —— 也就是说**内容解码器都在固件里**。

## 二、模拟器现状（`emul/wemu.py` / `emu/iter.py`）

做法：Unicorn(ARM) + 自建设备模型。

1. 把 `sword_elfd.bin` 载入 `0x30000000`，映射 32MB SDRAM。
2. **把开启 MMU 的那一条 MCR 改成 NOP**，让 CPU 跑物理地址（identity）。
3. 在地址 0 放一张**跳转向量表**（`ldr pc,[pc,#0x18]` + 目标），使 SVC/IRQ 能进到
   `0x30000100` / `0x30217618` 的真实 handler（原向量表是相对跳转，只有放在 0x30000000 才对）。
4. MMIO 用"写回读"寄存器文件模拟；少数 busy 状态位自动清零；`0x20020034` 强制"就绪"。
5. `svc` 用 INTR hook 吞掉（其 handler 本就是空 trampoline）。

结果：**固件可以持续跑下去**（实测 3 亿条指令无卡死），RTOS 已在多任务切换
（SP 在 0x31d26xxx 各任务栈之间变化），LCD 换页位在翻转（约 3700 次）。

但 **LCD 帧缓冲里没有画面**（`0x30000000` 仍是原始固件字节）——UI 还没起来。
最可能原因：**存储/文件系统初始化失败**（没有模拟 NAND/MMC），应用加载不到资源。

## 三、距离"能正常使用"还缺什么

按依赖顺序：

1. **存储**：NAND 控制器（FSLib/NandMtd）或 SD/MMC + FAT —— 需要让固件能读到
   `万虹书苑.dat` / `*.std` / `*.dic` / 字库等。工作量最大。
2. **LCD 面板模型**：面板 ID 探测要返回驱动期望的值，并给出真正的帧缓冲分配与分辨率。
3. **输入**：GPIO 按键矩阵 + 触摸 ADC。
4. **定时器/RTC 中断**：让调度器有 tick（目前靠忙等延时，能跑但不真实）。
5. **音频**（可选）。

## 四、运行

```bash
python3 emu/wemu.py 150000000        # 跑 1.5 亿条指令
python3 emu/iter.py 300000000        # 自动满足轮询寄存器的探索模式
python3 emu/fb2.py 120000000         # 尝试把帧缓冲导成 PNG
```

产物：`emu/sdram.bin`（SDRAM 快照）、`emu/fb_*.png`（帧缓冲候选渲染）、
`emu/*.log`（运行记录）、`emu/dump*.txt`（反汇编片段）。
