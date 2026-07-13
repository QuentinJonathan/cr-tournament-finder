const test = require('node:test');
const assert = require('node:assert/strict');

const { deriveTiming, formatCountdown, parseCrTime } = require('../static/timing.js');

const tournament = {
  status: 'inPreparation',
  createdTime: '20260714T100000.000Z',
  preparationDuration: 600,
  duration: 1800,
};

test('PREP exposes the phase and latest start countdown', () => {
  const timing = deriveTiming(tournament, Date.parse('2026-07-14T10:09:59.000Z'));
  assert.equal(timing.phase, 'prep');
  assert.equal(timing.effectiveStatus, 'inPreparation');
  assert.equal(timing.timingLabel, 'Starts by');
  assert.equal(timing.remainingSec, 1);
  assert.equal(timing.isEstimated, true);
});

test('stale PREP snapshot transitions to LIVE at the scheduled boundary', () => {
  const timing = deriveTiming(tournament, Date.parse('2026-07-14T10:10:00.000Z'));
  assert.equal(timing.phase, 'live');
  assert.equal(timing.effectiveStatus, 'inProgress');
  assert.equal(timing.timingLabel, 'Ends in');
  assert.equal(timing.remainingSec, 1800);
});

test('stale PREP snapshot eventually transitions to ended', () => {
  const timing = deriveTiming(tournament, Date.parse('2026-07-14T10:40:00.000Z'));
  assert.equal(timing.phase, 'ended');
  assert.equal(timing.effectiveStatus, 'ended');
  assert.equal(timing.remainingSec, 0);
});

test('actual early start produces an exact live end time', () => {
  const timing = deriveTiming({
    ...tournament,
    status: 'inProgress',
    startedTime: '20260714T100500.000Z',
  }, Date.parse('2026-07-14T10:10:00.000Z'));
  assert.equal(timing.phase, 'live');
  assert.equal(timing.remainingSec, 1500);
  assert.equal(timing.endsAtMs, Date.parse('2026-07-14T10:35:00.000Z'));
  assert.equal(timing.isEstimated, false);
});

test('live tournament without timing data stays LIVE but does not invent a timer', () => {
  const timing = deriveTiming({ status: 'inProgress', duration: 1800 }, Date.now());
  assert.equal(timing.phase, 'live');
  assert.equal(timing.remainingSec, null);
  assert.equal(formatCountdown(timing.remainingSec), '—');
});

test('raw ended status always wins', () => {
  const timing = deriveTiming({ ...tournament, status: 'ended' }, Date.parse('2026-07-14T10:01:00.000Z'));
  assert.equal(timing.phase, 'ended');
  assert.equal(timing.remainingSec, 0);
});

test('countdown formatting and CR timestamp validation are stable', () => {
  assert.equal(formatCountdown(5), '00:05');
  assert.equal(formatCountdown(3661), '1:01:01');
  assert.equal(formatCountdown(null), '—');
  assert.equal(parseCrTime('not-a-time'), null);
  assert.equal(parseCrTime('20260714T100000.000Z').toISOString(), '2026-07-14T10:00:00.000Z');
});
