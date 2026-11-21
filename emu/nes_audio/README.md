# NES 声音合成与执行加速

`make -C emu/nes_audio` 构建 `libp36nes.so` 和 `libp36slice.so`。前者负责合成 NES 声音，
后者提供浏览器 NES 模式的原生基本块预算回调，构建时使用当前 Python 环境中 Unicorn 的 C 头文件。
缺少 `libp36slice.so` 时，模拟器使用 Unicorn 的指令计数执行游戏。

`gme/` 包含 Game Music Emu 0.6.3 的 NES APU、振荡器及输出缓冲源码，取自提交
`e76bdc0cb916e79aa540290e6edd0c445879d3ba`，保留其许可证和版权声明。

```text
https://github.com/libgme/game-music-emu/tree/e76bdc0cb916e79aa540290e6edd0c445879d3ba
```
