const mimeType = "audio/mpeg";
const prebufferSeconds = 0.4;
const mutedChunks = 4;

export class Mp3Playback {
  constructor(setVolume, onError, displayedMovieSeconds) {
    this.setVolume = setVolume;
    this.onError = onError;
    this.displayedMovieSeconds = displayedMovieSeconds;
  }

  movieClock(track) {
    const stream = this.current;
    if (stream?.track !== track || !stream.output?.started || stream.output.movieSeconds === undefined) {
      return undefined;
    }
    return stream.output.movieSeconds + stream.output.element.currentTime;
  }

  movieState(track) {
    if (this.current?.track === track) return this.current.state;
    if (this.next?.track === track) return this.next.state;
    return undefined;
  }

  wantsMovieAudio(track) {
    return Boolean(this.context && this.current?.track === track && this.current.movie);
  }

  setOutput(context, gain) {
    this.context = context;
    this.gain = gain;
    if (!context) {
      this.closeOutput(this.current);
      this.current = this.next ?? this.current;
      this.next = undefined;
    }
    this.pump();
  }

  receive({ track, state, contents, volume, movieSeconds }) {
    if (state === "stopped") {
      if (this.next?.track === track) this.next = undefined;
      if (this.current?.track === track) this.advance();
      return;
    }

    let stream = this.current;
    if (stream?.track !== track) {
      const draining = stream?.state === "ended" && stream.output && !stream.output.element.ended;
      if (draining) {
        if (this.next?.track !== track) this.next = { track, chunks: [] };
        stream = this.next;
      } else {
        this.closeOutput(stream);
        this.next = undefined;
        stream = this.current = { track, chunks: [] };
      }
    }
    stream.state = state;
    stream.volume = volume;
    if (movieSeconds !== undefined) stream.movie = true;
    if (contents.length) {
      stream.chunks.push({ contents, movieSeconds });
      if (!this.context && stream.chunks.length > mutedChunks) stream.chunks.shift();
    }
    if (stream === this.current) this.pump();
  }

  clear() {
    this.closeOutput(this.current);
    this.current = undefined;
    this.next = undefined;
  }

  advance() {
    this.closeOutput(this.current);
    this.current = this.next;
    this.next = undefined;
    this.pump();
  }

  openOutput(stream) {
    if (!globalThis.MediaSource?.isTypeSupported(mimeType)) {
      throw new Error("此浏览器不支持 MP3 流式播放。");
    }
    const media = new MediaSource();
    const element = new Audio();
    const node = this.context.createMediaElementSource(element);
    node.connect(this.gain);
    const url = URL.createObjectURL(media);
    const movieSeconds = stream.chunks[0].movieSeconds;
    const output = { media, element, node, url, movieSeconds, opened: performance.now() };
    stream.output = output;
    media.addEventListener("sourceopen", () => {
      if (stream.output !== output) return;
      try {
        output.buffer = media.addSourceBuffer(mimeType);
        output.buffer.addEventListener("updateend", () => this.pump());
        this.pump();
      } catch (error) {
        this.fail(error);
      }
    }, { once: true });
    element.addEventListener("ended", () => {
      if (stream.output === output) this.advance();
    });
    element.addEventListener("playing", () => { output.started = true; });
    element.addEventListener("error", () => {
      if (stream.output === output) this.fail(new Error("浏览器无法解码 MP3 音频。"));
    });
    element.src = url;
    if (stream.movie) setTimeout(() => {
      if (stream.output === output) this.pump();
    }, firstPictureWaitMs);
  }

  closeOutput(stream) {
    if (!stream?.output) return;
    const { element, node, url } = stream.output;
    stream.output = undefined;
    element.pause();
    element.removeAttribute("src");
    element.load();
    node.disconnect();
    URL.revokeObjectURL(url);
  }

  pump() {
    const stream = this.current;
    if (!stream || !this.context) return;
    try {
      if (!stream.output && stream.chunks.length) this.openOutput(stream);
      if (!stream.output) return;
      const output = stream.output;
      const { buffer, element, media } = output;
      this.setVolume(stream.volume);
      if (stream.state === "paused") element.pause();
      if (!buffer || buffer.updating || media.readyState === "closed") return;

      const ranges = buffer.buffered;
      const bufferedEnd = ranges.length ? ranges.end(ranges.length - 1) : 0;
      if (media.readyState === "open") {
        if (ranges.length && ranges.start(0) < element.currentTime - 60) {
          buffer.remove(0, element.currentTime - 30);
          return;
        }
        if (stream.chunks.length) {
          buffer.appendBuffer(stream.chunks.shift().contents);
        } else if (stream.state === "ended") {
          media.endOfStream();
        }
      }
      const required = stream.state === "ended" ? 0 : prebufferSeconds;
      const picture = stream.movie ? this.displayedMovieSeconds(stream.track) : undefined;
      if (stream.movie && picture === undefined && performance.now() - output.opened < firstPictureWaitMs) return;
      if (!output.started && output.movieSeconds !== undefined && picture !== undefined) {
        const seek = Math.max(0, picture - output.movieSeconds);
        if (seek > element.currentTime && bufferedEnd > seek + required) element.currentTime = seek;
      }
      if (stream.state !== "paused" && element.paused && !element.ended
          && bufferedEnd - element.currentTime > required) {
        element.play().catch(error => {
          if (stream.output === output && error.name !== "AbortError") this.fail(error);
        });
      }
    } catch (error) {
      this.fail(error);
    }
  }

  fail(error) {
    this.clear();
    this.onError(error.message);
  }
}
import { firstPictureWaitMs } from "./movie-playback.js";

