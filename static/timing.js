// Shared tournament timing model for the browser and Node-based tests.
var CrTiming = (() => {
  function parseCrTime(value) {
    if (typeof value !== 'string') return null;
    const match = value.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})\.(\d{3})Z$/);
    if (!match) return null;
    const [, y, m, d, hh, mm, ss, ms] = match;
    const date = new Date(`${y}-${m}-${d}T${hh}:${mm}:${ss}.${ms}Z`);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function positiveSeconds(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : 0;
  }

  function secondsUntil(targetMs, nowMs) {
    return Math.max(0, Math.ceil((targetMs - nowMs) / 1000));
  }

  function result({
    phase,
    countdownType,
    remainingSec,
    totalSec,
    startsAtMs = null,
    endsAtMs = null,
    actualStartedAtMs = null,
    isEstimated = false,
  }) {
    const effectiveStatus = phase === 'prep'
      ? 'inPreparation'
      : phase === 'live'
        ? 'inProgress'
        : phase === 'ended'
          ? 'ended'
          : 'unknown';
    const timingLabel = countdownType === 'starts'
      ? 'Starts by'
      : countdownType === 'ends'
        ? 'Ends in'
        : countdownType === 'ended'
          ? 'Ended'
          : 'Time unavailable';

    return {
      phase,
      effectiveStatus,
      countdownType,
      timingLabel,
      remainingSec,
      totalSec: Math.max(1, totalSec || 0),
      startsAtMs,
      endsAtMs,
      actualStartedAtMs,
      nextAtMs: countdownType === 'starts' ? startsAtMs : countdownType === 'ends' ? endsAtMs : null,
      isEstimated,
    };
  }

  function deriveTiming(tournament, nowMs = Date.now()) {
    const t = tournament || {};
    const durationSec = positiveSeconds(t.duration);
    const prepSec = positiveSeconds(t.preparationDuration);
    const created = parseCrTime(t.createdTime);
    const actualStarted = parseCrTime(t.startedTime);
    const createdAtMs = created ? created.getTime() : null;
    const actualStartedAtMs = actualStarted ? actualStarted.getTime() : null;
    const scheduledStartAtMs = createdAtMs === null ? null : createdAtMs + prepSec * 1000;
    const startsAtMs = actualStartedAtMs ?? scheduledStartAtMs;
    const endsAtMs = startsAtMs === null ? null : startsAtMs + durationSec * 1000;

    if (t.status === 'ended') {
      return result({
        phase: 'ended',
        countdownType: 'ended',
        remainingSec: 0,
        totalSec: durationSec,
        startsAtMs,
        endsAtMs,
        actualStartedAtMs,
      });
    }

    if (t.status === 'inPreparation') {
      if (scheduledStartAtMs === null) {
        return result({
          phase: 'prep',
          countdownType: 'starts',
          remainingSec: null,
          totalSec: prepSec,
          isEstimated: true,
        });
      }
      if (nowMs < scheduledStartAtMs) {
        return result({
          phase: 'prep',
          countdownType: 'starts',
          remainingSec: secondsUntil(scheduledStartAtMs, nowMs),
          totalSec: prepSec,
          startsAtMs: scheduledStartAtMs,
          endsAtMs: scheduledStartAtMs + durationSec * 1000,
          isEstimated: true,
        });
      }

      // The API snapshot may still say PREP. Once the latest possible start
      // has passed, continue from the scheduled boundary instead of sticking
      // at 00:00 until the next crawl.
      const scheduledEndAtMs = scheduledStartAtMs + durationSec * 1000;
      if (durationSec > 0 && nowMs < scheduledEndAtMs) {
        return result({
          phase: 'live',
          countdownType: 'ends',
          remainingSec: secondsUntil(scheduledEndAtMs, nowMs),
          totalSec: durationSec,
          startsAtMs: scheduledStartAtMs,
          endsAtMs: scheduledEndAtMs,
          isEstimated: true,
        });
      }
      return result({
        phase: 'ended',
        countdownType: 'ended',
        remainingSec: 0,
        totalSec: durationSec,
        startsAtMs: scheduledStartAtMs,
        endsAtMs: scheduledEndAtMs,
        isEstimated: true,
      });
    }

    if (t.status === 'inProgress') {
      if (endsAtMs === null) {
        return result({
          phase: 'live',
          countdownType: 'ends',
          remainingSec: null,
          totalSec: durationSec,
          actualStartedAtMs,
          isEstimated: true,
        });
      }
      if (durationSec > 0 && nowMs < endsAtMs) {
        return result({
          phase: 'live',
          countdownType: 'ends',
          remainingSec: secondsUntil(endsAtMs, nowMs),
          totalSec: durationSec,
          startsAtMs,
          endsAtMs,
          actualStartedAtMs,
          isEstimated: actualStartedAtMs === null,
        });
      }
      return result({
        phase: 'ended',
        countdownType: 'ended',
        remainingSec: 0,
        totalSec: durationSec,
        startsAtMs,
        endsAtMs,
        actualStartedAtMs,
        isEstimated: actualStartedAtMs === null,
      });
    }

    return result({
      phase: 'unknown',
      countdownType: 'unknown',
      remainingSec: null,
      totalSec: durationSec,
      startsAtMs,
      endsAtMs,
      actualStartedAtMs,
      isEstimated: true,
    });
  }

  function formatCountdown(seconds) {
    if (!Number.isFinite(seconds)) return '—';
    if (seconds <= 0) return '00:00';
    const rounded = Math.ceil(seconds);
    const hours = Math.floor(rounded / 3600);
    const minutes = Math.floor((rounded % 3600) / 60);
    const secs = rounded % 60;
    const pad = number => String(number).padStart(2, '0');
    return hours > 0 ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${pad(minutes)}:${pad(secs)}`;
  }

  return { deriveTiming, formatCountdown, parseCrTime };
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = CrTiming;
}
