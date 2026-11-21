const maxFrames = 32;
export const firstPictureWaitMs = 1500;

export class MoviePlayback {
  constructor(paint, audio, onPicture) {
    this.paint = paint;
    this.audio = audio;
    this.onPicture = onPicture;
  }

  clear() {
    cancelAnimationFrame(this.animation);
    this.animation = undefined;
    this.current = undefined;
    this.next = undefined;
    this.menu = undefined;
  }

  displayedSeconds(track) {
    return this.current?.track === track ? this.current.displayed : undefined;
  }

  receive(frame) {
    const current = this.current;
    if (frame.track === undefined) {
      if (current && this.audio.movieClock(current.track) !== undefined) {
        this.menu = frame;
        this.schedule();
      } else {
        this.clear();
        this.draw(frame);
      }
      return;
    }

    this.menu = undefined;
    let movie = current;
    if (current?.track !== frame.track) {
      if (current && this.audio.movieClock(current.track) !== undefined) {
        if (this.next?.track !== frame.track) this.next = this.newTrack(frame.track);
        movie = this.next;
      } else {
        this.next = undefined;
        movie = this.current = this.newTrack(frame.track);
      }
    }
    const seconds = frame.milliseconds / 1000;
    if (seconds > (movie.frames.at(-1)?.seconds ?? movie.displayed ?? -1)) {
      movie.frames.push({ seconds, frame });
      if (movie.frames.length > maxFrames) movie.frames.shift();
    }
    this.schedule();
  }

  newTrack(track) {
    return { track, frames: [], created: performance.now() };
  }

  draw({ width, height, pixels }) {
    this.paint(width, height, pixels);
  }

  schedule() {
    if (this.animation !== undefined) return;
    this.animation = requestAnimationFrame(now => this.present(now));
  }

  present(now) {
    this.animation = undefined;
    let movie = this.current;
    if (!movie) return;
    let audioSeconds = this.audio.movieClock(movie.track);
    if (audioSeconds === undefined) {
      if (this.menu) {
        const frame = this.menu;
        this.clear();
        this.draw(frame);
        return;
      }
      if (this.next) {
        movie = this.current = this.next;
        this.next = undefined;
        audioSeconds = this.audio.movieClock(movie.track);
      }
    }

    if (movie.displayed === undefined && movie.frames.length) {
      const first = movie.frames.shift();
      movie.displayed = first.seconds;
      this.draw(first.frame);
      this.onPicture();
    }
    const paused = this.audio.movieState(movie.track) === "paused";
    const waiting = this.audio.wantsMovieAudio(movie.track)
      && audioSeconds === undefined && now - movie.created < firstPictureWaitMs;
    if (audioSeconds !== undefined || paused || waiting || !movie.anchor) {
      movie.anchor = { seconds: audioSeconds ?? movie.displayed, wall: now };
    }
    const time = movie.anchor.seconds + (paused || waiting ? 0 : (now - movie.anchor.wall) / 1000);
    let frame;
    while (movie.frames.at(0)?.seconds <= time + 0.008) frame = movie.frames.shift();
    if (frame) {
      movie.displayed = frame.seconds;
      this.draw(frame.frame);
    }
    if (movie.frames.length || this.next || this.menu) this.schedule();
  }
}
