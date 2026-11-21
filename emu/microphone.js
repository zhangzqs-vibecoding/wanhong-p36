class P36Microphone extends AudioWorkletProcessor {
  constructor() {
    super();
    this.block = new Int16Array(320);
    this.samples = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const sample of channel) {
      this.block[this.samples++] = Math.round(Math.max(-1, Math.min(1, sample)) * 32767);
      if (this.samples === this.block.length) {
        this.port.postMessage(this.block.buffer, [this.block.buffer]);
        this.block = new Int16Array(320);
        this.samples = 0;
      }
    }
    return true;
  }
}

registerProcessor("p36-microphone", P36Microphone);
