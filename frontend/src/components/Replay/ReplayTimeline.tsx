import { useReplayStore } from "../../store/replay";
import { describeEvent, formatRelativeTime } from "../../lib/eventLabels";

const SPEEDS = [0.5, 1, 2, 4];

/** U15 (v2 upgrade), Incident Replay's step-through control — the
 * scrubber/transport half of FRONTEND_V2.md's flagship #3, paired with
 * IncidentReplayPage's playback-driven graph re-fold. Every label and
 * timestamp here comes straight from the real `events` table (via
 * GET /traces/{id}/events) — no synthetic replay data. */
export function ReplayTimeline() {
  const events = useReplayStore((s) => s.events);
  const playheadIndex = useReplayStore((s) => s.playheadIndex);
  const playing = useReplayStore((s) => s.playing);
  const speed = useReplayStore((s) => s.speed);
  const setPlayheadIndex = useReplayStore((s) => s.setPlayheadIndex);
  const play = useReplayStore((s) => s.play);
  const pause = useReplayStore((s) => s.pause);
  const restart = useReplayStore((s) => s.restart);
  const setSpeed = useReplayStore((s) => s.setSpeed);

  if (events.length === 0) return null;

  const t0 = Date.parse(events[0]!.created_at);

  return (
    <div className="replay-timeline">
      <div className="replay-timeline__controls">
        <button onClick={restart} title="Restart" aria-label="Restart">
          ⏮
        </button>
        <button
          onClick={playing ? pause : play}
          title={playing ? "Pause" : "Play"}
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? "⏸" : "▶"}
        </button>
        <input
          type="range"
          min={-1}
          max={events.length - 1}
          value={playheadIndex}
          onChange={(e) => setPlayheadIndex(Number(e.target.value))}
          className="replay-timeline__scrubber"
        />
        <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
          {SPEEDS.map((s) => (
            <option key={s} value={s}>
              {s}×
            </option>
          ))}
        </select>
      </div>

      <div className="replay-timeline__steps" role="list" aria-label="Replay steps">
        {events.map((event, i) => (
          <button
            key={event.event_id}
            role="listitem"
            className={`replay-timeline__step${i <= playheadIndex ? " replay-timeline__step--played" : ""}${
              i === playheadIndex ? " replay-timeline__step--current" : ""
            }`}
            onClick={() => setPlayheadIndex(i)}
          >
            <span className="replay-timeline__step-time">
              {formatRelativeTime(Date.parse(event.created_at) - t0)}
            </span>
            <span className="replay-timeline__step-label">{describeEvent(event)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
