/**
 * Simple Web Audio API tone generator for station operator feedback.
 * No external audio files needed.
 */

let ctx: AudioContext | null = null;

function getCtx(): AudioContext {
  if (!ctx) {
    ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
  }
  return ctx;
}

function playTone(
  freq: number,
  duration: number,
  type: OscillatorType = "sine",
  volume = 0.12,
) {
  try {
    const c = getCtx();
    if (c.state === "suspended") c.resume();
    const osc = c.createOscillator();
    const gain = c.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    gain.gain.value = volume;
    osc.connect(gain).connect(c.destination);
    osc.start();
    gain.gain.exponentialRampToValueAtTime(0.001, c.currentTime + duration);
    osc.stop(c.currentTime + duration);
  } catch {
    // Audio not available — silently ignore
  }
}

export const Sound = {
  /** Two-tone chime when robot arrives at station */
  robotArrived: () => {
    playTone(880, 0.12);
    setTimeout(() => playTone(1100, 0.12), 130);
  },

  /** Short high beep on successful scan */
  scanSuccess: () => playTone(1200, 0.08),

  /** Low buzz on scan error */
  scanError: () => {
    playTone(300, 0.12, "square");
    setTimeout(() => playTone(200, 0.18, "square"), 150);
  },

  /** Medium tone when tote is bound */
  toteBound: () => playTone(660, 0.1),
};
