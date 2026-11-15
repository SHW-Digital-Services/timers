import csv
import json
import math
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import streamlit as st


HISTORY_FILE = Path(__file__).with_name("timer_history.csv")
HISTORY_FIELDS = (
    "timestamp",
    "session_id",
    "event",
    "timer",
    "phase",
    "duration_seconds",
    "remaining_seconds",
)
PRESET_TIMERS = {
    "1-hour timer": 60 * 60,
    "25-minute timer": 25 * 60,
    "5-minute timer": 5 * 60,
}
CUSTOM_TIMER_MODE = "Custom timer"
POMODORO_MODE = "Pomodoro"
SOUND_FILES = {
    "Work": Path(__file__).with_name("sounds") / "work.mp3",
    "Short break": Path(__file__).with_name("sounds") / "shortbreak.mp3",
    "Long break": Path(__file__).with_name("sounds") / "longbreak.mp3",
}


def _log_history_event(
    timer: dict[str, int | float | str],
    event: str,
    *,
    remaining_seconds: int | float | None = None,
) -> None:
    write_header = not HISTORY_FILE.exists() or HISTORY_FILE.stat().st_size == 0
    with HISTORY_FILE.open("a", newline="", encoding="utf-8") as history_file:
        writer = csv.DictWriter(history_file, fieldnames=HISTORY_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                "session_id": timer["session_id"],
                "event": event,
                "timer": timer["mode"],
                "phase": timer["phase"],
                "duration_seconds": timer["duration_seconds"],
                "remaining_seconds": (
                    "" if remaining_seconds is None else round(remaining_seconds)
                ),
            }
        )


def _start_timer(
    mode: str,
    duration_seconds: int,
    *,
    work_seconds: int | None = None,
    short_break_seconds: int | None = None,
    long_break_seconds: int | None = None,
    sessions_before_long_break: int = 3,
    total_focus_seconds: int | None = None,
) -> None:
    if total_focus_seconds is not None:
        duration_seconds = min(duration_seconds, total_focus_seconds)

    timer = {
        "mode": mode,
        "phase": "Work" if mode == POMODORO_MODE else mode,
        "duration_seconds": duration_seconds,
        "deadline": time.monotonic() + duration_seconds,
        "status": "running",
        "remaining_seconds": duration_seconds,
        "completed_work_sessions": 0,
        "session_id": uuid4().hex,
    }

    if mode == POMODORO_MODE:
        timer.update(
            work_seconds=work_seconds,
            short_break_seconds=short_break_seconds,
            long_break_seconds=long_break_seconds,
            sessions_before_long_break=sessions_before_long_break,
        )
        if total_focus_seconds is not None:
            timer.update(
                total_focus_seconds=total_focus_seconds,
                completed_focus_seconds=0,
                completed_poms=0,
            )

    st.session_state["timer"] = timer
    _log_history_event(timer, "started", remaining_seconds=duration_seconds)


def _calculate_pomodoro_plan(
    total_focus_seconds: int,
    work_seconds: int,
    short_break_seconds: int,
    long_break_seconds: int,
    sessions_before_long_break: int,
) -> dict[str, int]:
    planned_poms = (total_focus_seconds + work_seconds - 1) // work_seconds
    planned_breaks = planned_poms - 1
    long_breaks = planned_breaks // sessions_before_long_break
    short_breaks = planned_breaks - long_breaks
    total_duration_seconds = (
        total_focus_seconds
        + short_breaks * short_break_seconds
        + long_breaks * long_break_seconds
    )
    return {
        "planned_poms": planned_poms,
        "short_breaks": short_breaks,
        "long_breaks": long_breaks,
        "total_duration_seconds": total_duration_seconds,
    }


def _next_pomodoro_work_seconds(timer: dict[str, int | float | str]) -> int:
    work_seconds = int(timer["work_seconds"])
    if "total_focus_seconds" in timer:
        remaining_focus_seconds = int(timer["total_focus_seconds"]) - int(
            timer["completed_focus_seconds"]
        )
        return min(work_seconds, remaining_focus_seconds)
    return work_seconds


def _advance_pomodoro(timer: dict[str, int | float | str]) -> None:
    completed_phase = timer["phase"]
    _log_history_event(timer, "phase_completed", remaining_seconds=0)
    if completed_phase == "Long break":
        if "total_focus_seconds" in timer:
            next_phase = "Work"
            next_duration = _next_pomodoro_work_seconds(timer)
        else:
            timer["status"] = "awaiting_continue"
            timer["remaining_seconds"] = 0
            st.toast("Long break complete.")
            return
    elif completed_phase == "Work":
        if "total_focus_seconds" in timer:
            timer["completed_focus_seconds"] = (
                int(timer["completed_focus_seconds"])
                + int(timer["duration_seconds"])
            )
            timer["completed_poms"] = int(timer["completed_poms"]) + 1
            if int(timer["completed_focus_seconds"]) >= int(
                timer["total_focus_seconds"]
            ):
                timer["status"] = "complete"
                timer["phase"] = "Pomodoro"
                timer["remaining_seconds"] = 0
                _log_history_event(timer, "completed", remaining_seconds=0)
                st.toast("Focus plan complete.")
                return

        completed_sessions = int(timer["completed_work_sessions"]) + 1
        timer["completed_work_sessions"] = completed_sessions
        if completed_sessions == int(timer["sessions_before_long_break"]):
            next_phase = "Long break"
            next_duration = int(timer["long_break_seconds"])
            timer["completed_work_sessions"] = 0
        else:
            next_phase = "Short break"
            next_duration = int(timer["short_break_seconds"])
    else:
        next_phase = "Work"
        next_duration = _next_pomodoro_work_seconds(timer)

    timer["phase"] = next_phase
    timer["duration_seconds"] = next_duration
    timer["remaining_seconds"] = next_duration
    timer["deadline"] = time.monotonic() + next_duration
    st.toast(f"{completed_phase} complete. Starting {next_phase.lower()}.")


def _render_audio_tracks() -> None:
    with st.expander("Session audio"):
        for phase, sound_file in SOUND_FILES.items():
            if not sound_file.is_file():
                raise FileNotFoundError(f"Timer sound file not found: {sound_file}")
            st.audio(
                sound_file,
                format="audio/mpeg",
                loop=True,
                alt=f"{phase} session audio",
            )


def _render_session_audio(timer: dict[str, int | float | str] | None) -> None:
    if timer is None or timer["status"] in {"complete", "awaiting_continue"}:
        phase = "Work"
        status = "idle"
        phase_key = f"idle:{phase}"
        remaining_seconds = 0
        start_on_click = True
    else:
        phase = str(timer["phase"])
        status = str(timer["status"])
        phase_key = f"{timer['session_id']}:{phase}"
        if status == "running":
            remaining_seconds = max(
                0, float(timer["deadline"]) - time.monotonic()
            )
        else:
            remaining_seconds = float(timer["remaining_seconds"])
        start_on_click = status == "paused"

    player_config = json.dumps(
        {
            "phaseKey": phase_key,
            "phase": phase,
            "status": status,
            "remainingSeconds": remaining_seconds,
            "startOnClick": start_on_click,
        }
    )
    st.html(
        f"""
        <div id="focus-timer-audio-status" role="status"></div>
        <script>
        (() => {{
          const config = {player_config};
          let player = window.__focusTimerAudio;
          if (!player) {{
            player = {{
              phaseKey: null,
              endAt: 0,
              phase: "Work",
              startOnClick: false,
              status: "idle",
              audioForPhase(phase) {{
                const audioPlayers = document.querySelectorAll("audio");
                const playerIndex = {{
                  "Work": 0,
                  "Short break": 1,
                  "Long break": 2,
                }}[phase] ?? 0;
                return audioPlayers[playerIndex] || null;
              }},
              update(next) {{
                const phaseChanged = this.phaseKey !== next.phaseKey;
                this.phaseKey = next.phaseKey;
                this.phase = next.phase;
                this.status = next.status;
                this.startOnClick = next.startOnClick;
                this.endAt = performance.now() + next.remainingSeconds * 1000;
                this.audio = this.audioForPhase(this.phase);
                if (!this.audio) return;
                this.audio.loop = true;
                if (phaseChanged) this.audio.volume = 1;
                document.querySelectorAll("audio").forEach((audio) => {{
                  if (audio !== this.audio) audio.pause();
                }});
                if (next.status === "running") {{
                  if (phaseChanged || this.audio.paused) this.play();
                }} else {{
                  this.audio.pause();
                  this.audio.volume = 1;
                }}
              }},
              play() {{
                this.audio = this.audioForPhase(this.phase);
                if (!this.audio) return;
                this.audio.loop = true;
                this.audio.play().catch(() => {{
                  const status = document.getElementById(
                    "focus-timer-audio-status"
                  );
                  if (status) {{
                    status.textContent =
                      "If playback is blocked, press play in the audio player.";
                  }}
                }});
              }},
              fade() {{
                if (!this.audio || this.status !== "running") return;
                const secondsLeft = Math.max(
                  0,
                  (this.endAt - performance.now()) / 1000
                );
                this.audio.volume = Math.min(1, secondsLeft / 5);
              }},
            }};
            window.__focusTimerAudio = player;
            document.addEventListener(
              "click",
              (event) => {{
                const button = event.target.closest("button");
                if (
                  button &&
                  player.startOnClick &&
                  ["Start timer", "Continue focusing", "Resume"].includes(
                    button.innerText.trim()
                  )
                ) {{
                  player.play();
                }}
              }},
              true
            );
            window.setInterval(() => {{
              player.audio = player.audioForPhase(player.phase);
              document.querySelectorAll("audio").forEach((audio) => {{
                if (audio !== player.audio) audio.pause();
              }});
              player.fade();
            }}, 50);
          }}
          player.update(config);
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def _format_remaining_time(remaining_seconds: int) -> str:
    hours, remainder = divmod(remaining_seconds, 60 * 60)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _render_clock(
    phase: str,
    remaining_seconds: int,
    duration_seconds: int,
    *,
    elapsed_seconds: float,
    is_running: bool,
) -> None:
    display_time = _format_remaining_time(remaining_seconds)
    remaining_percent = 100 * remaining_seconds / duration_seconds
    circumference = 2 * math.pi * 108
    progress_length = circumference * remaining_percent / 100
    second_angle = (elapsed_seconds % 60) * 6
    minute_angle = (elapsed_seconds % 3600) * 0.1
    hour_angle = (elapsed_seconds % 43200) / 120

    tick_marks = []
    for tick in range(60):
        angle = math.radians(tick * 6 - 90)
        outer_radius = 94
        inner_radius = outer_radius - (9 if tick % 5 == 0 else 4)
        tick_marks.append(
            (
                f'<line x1="{120 + inner_radius * math.cos(angle):.2f}" '
                f'y1="{120 + inner_radius * math.sin(angle):.2f}" '
                f'x2="{120 + outer_radius * math.cos(angle):.2f}" '
                f'y2="{120 + outer_radius * math.sin(angle):.2f}" '
                f'class="clock-tick{" clock-hour-tick" if tick % 5 == 0 else ""}" />'
            )
        )

    st.html(
        f"""
        <style>
          .timer-clock-wrap {{
            display: flex;
            justify-content: center;
            padding: 0.75rem 0 1.25rem;
          }}
          .timer-clock {{
            position: relative;
            width: min(100%, 19rem);
            aspect-ratio: 1;
            border-radius: 50%;
            background: radial-gradient(
              circle,
              color-mix(in srgb, currentColor 5%, transparent),
              transparent 72%
            );
          }}
          .timer-clock svg {{
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            overflow: visible;
          }}
          .timer-clock-ring {{
            fill: none;
            stroke: currentColor;
            stroke-width: 4;
          }}
          .timer-clock-track {{
            opacity: 0.16;
          }}
          .timer-clock-progress {{
            stroke-linecap: round;
            transform: rotate(-90deg);
            transform-origin: 120px 120px;
          }}
          .timer-clock-hand {{
            fill: none;
            stroke: currentColor;
            stroke-linecap: round;
            transform-box: view-box;
            transform-origin: center;
          }}
          .clock-hour-hand {{
            stroke-width: 5;
            opacity: 0.85;
          }}
          .clock-minute-hand {{
            stroke-width: 3.5;
            opacity: 0.9;
          }}
          .clock-second-hand {{
            stroke: #e11d48;
            stroke-width: 2;
          }}
          .clock-center-cap {{
            fill: #e11d48;
          }}
          @keyframes clock-hand-turn {{
            from {{ transform: rotate(0deg); }}
            to {{ transform: rotate(360deg); }}
          }}
          @media (prefers-reduced-motion: no-preference) {{
            .timer-clock-hand.is-running {{
              animation-name: clock-hand-turn;
              animation-timing-function: linear;
              animation-iteration-count: infinite;
            }}
            .clock-second-hand.is-running {{
              animation-duration: 60s;
              animation-delay: -{elapsed_seconds % 60:.3f}s;
            }}
            .clock-minute-hand.is-running {{
              animation-duration: 3600s;
              animation-delay: -{elapsed_seconds % 3600:.3f}s;
            }}
            .clock-hour-hand.is-running {{
              animation-duration: 43200s;
              animation-delay: -{elapsed_seconds % 43200:.3f}s;
            }}
          }}
          .clock-tick {{
            stroke: currentColor;
            stroke-width: 1.5;
            opacity: 0.28;
          }}
          .clock-hour-tick {{
            stroke-width: 2.5;
            opacity: 0.65;
          }}
          .clock-number {{
            fill: currentColor;
            font: 600 12px system-ui, sans-serif;
            text-anchor: middle;
            dominant-baseline: middle;
            opacity: 0.75;
          }}
          .timer-clock-center {{
            position: absolute;
            inset: 0;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            pointer-events: none;
          }}
          .timer-clock-time {{
            font-size: clamp(2rem, 9vw, 3.75rem);
            font-variant-numeric: tabular-nums;
            font-weight: 650;
            letter-spacing: -0.06em;
            line-height: 1;
          }}
          .timer-clock-caption {{
            margin-top: 0.5rem;
            font-size: 0.8rem;
            opacity: 0.7;
          }}
          .timer-clock-accessible-progress {{
            position: absolute;
            width: 1px;
            height: 1px;
            overflow: hidden;
            clip-path: inset(50%);
            white-space: nowrap;
          }}
          @media (prefers-reduced-motion: reduce) {{
            .timer-clock-progress {{
              transition: none;
            }}
          }}
        </style>
        <div class="timer-clock-wrap">
          <div class="timer-clock">
            <progress
              class="timer-clock-accessible-progress"
              max="100"
              value="{remaining_percent:.2f}"
              aria-label="{phase} time remaining"
              aria-valuetext="{display_time} remaining"
            ></progress>
            <svg viewBox="0 0 240 240" aria-hidden="true">
              <circle
                class="timer-clock-ring timer-clock-track"
                cx="120" cy="120" r="108"
              />
              <circle
                class="timer-clock-ring timer-clock-progress"
                cx="120" cy="120" r="108"
                stroke-dasharray="{progress_length:.2f} {circumference:.2f}"
              />
              {''.join(tick_marks)}
              <g
                class="timer-clock-hand clock-hour-hand{' is-running' if is_running else ''}"
                style="transform: rotate({hour_angle:.3f}deg)"
              >
                <line x1="120" y1="120" x2="120" y2="84" />
              </g>
              <g
                class="timer-clock-hand clock-minute-hand{' is-running' if is_running else ''}"
                style="transform: rotate({minute_angle:.3f}deg)"
              >
                <line x1="120" y1="120" x2="120" y2="65" />
              </g>
              <g
                class="timer-clock-hand clock-second-hand{' is-running' if is_running else ''}"
                style="transform: rotate({second_angle:.3f}deg)"
              >
                <line x1="120" y1="136" x2="120" y2="52" />
              </g>
              <text class="clock-number" x="120" y="46">12</text>
              <text class="clock-number" x="194" y="120">3</text>
              <text class="clock-number" x="120" y="194">6</text>
              <text class="clock-number" x="46" y="120">9</text>
              <circle class="clock-center-cap" cx="120" cy="120" r="4" />
            </svg>
            <div class="timer-clock-center" aria-hidden="true">
              <div class="timer-clock-time">{display_time}</div>
              <div class="timer-clock-caption">remaining</div>
            </div>
          </div>
        </div>
        """,
        width="stretch",
    )


@st.fragment(run_every="1s")
def _render_timer() -> None:
    timer = st.session_state.get("timer")
    if timer is None:
        st.info("Choose a timer and start when you're ready.")
        _render_session_audio(None)
        return

    if timer["status"] == "running":
        exact_remaining_seconds = max(
            0.0, float(timer["deadline"]) - time.monotonic()
        )
        remaining_seconds = math.ceil(exact_remaining_seconds)
        if remaining_seconds == 0:
            if timer["mode"] == POMODORO_MODE:
                _advance_pomodoro(timer)
                remaining_seconds = int(timer["duration_seconds"])
                exact_remaining_seconds = float(timer["duration_seconds"])
            else:
                timer["status"] = "complete"
                timer["remaining_seconds"] = 0
                exact_remaining_seconds = 0
                _log_history_event(timer, "completed", remaining_seconds=0)
                st.toast("Timer complete.")
    elif timer["status"] == "awaiting_continue":
        remaining_seconds = 0
        exact_remaining_seconds = 0
    else:
        exact_remaining_seconds = max(0.0, float(timer["remaining_seconds"]))
        remaining_seconds = math.ceil(exact_remaining_seconds)

    if timer["status"] == "awaiting_continue":
        st.success("Long break complete.")
        st.write("Would you like to continue with another focus session?")
        continue_column, finish_column = st.columns(2)
        if continue_column.button(
            "Continue focusing", key="continue_pomodoro", width="stretch"
        ):
            timer["phase"] = "Work"
            timer["duration_seconds"] = int(timer["work_seconds"])
            timer["remaining_seconds"] = int(timer["work_seconds"])
            timer["deadline"] = time.monotonic() + int(timer["work_seconds"])
            timer["status"] = "running"
            _log_history_event(
                timer, "continued", remaining_seconds=timer["work_seconds"]
            )
            st.rerun()
        if finish_column.button(
            "Finish for now", key="finish_pomodoro", width="stretch"
        ):
            timer["status"] = "complete"
            timer["phase"] = "Pomodoro"
            _log_history_event(timer, "finished", remaining_seconds=0)
            st.rerun()
    elif timer["status"] == "complete":
        if "total_focus_seconds" in timer:
            st.success("Focus plan complete!")
        else:
            st.success(f"{timer['phase']} complete!")
        _render_clock(
            str(timer["phase"]),
            0,
            int(timer["duration_seconds"]),
            elapsed_seconds=float(timer["duration_seconds"]),
            is_running=False,
        )
    else:
        st.subheader(timer["phase"])
        if "total_focus_seconds" in timer:
            plan = _calculate_pomodoro_plan(
                int(timer["total_focus_seconds"]),
                int(timer["work_seconds"]),
                int(timer["short_break_seconds"]),
                int(timer["long_break_seconds"]),
                int(timer["sessions_before_long_break"]),
            )
            st.caption(
                f"Focus session {int(timer['completed_poms']) + 1} "
                f"of {plan['planned_poms']}"
            )
        _render_clock(
            str(timer["phase"]),
            remaining_seconds,
            int(timer["duration_seconds"]),
            elapsed_seconds=max(
                0.0, float(timer["duration_seconds"]) - exact_remaining_seconds
            ),
            is_running=timer["status"] == "running",
        )

    if timer["status"] == "running":
        pause_column, stop_column = st.columns(2)
        if pause_column.button("Pause", key="pause_timer", width="stretch"):
            timer["remaining_seconds"] = max(
                0, float(timer["deadline"]) - time.monotonic()
            )
            timer["status"] = "paused"
            _log_history_event(
                timer, "paused", remaining_seconds=timer["remaining_seconds"]
            )
            st.rerun()
        if stop_column.button("Stop", key="stop_timer", width="stretch"):
            _log_history_event(
                timer,
                "stopped",
                remaining_seconds=max(
                    0, float(timer["deadline"]) - time.monotonic()
                ),
            )
            st.session_state["timer"] = None
            st.rerun()
    elif timer["status"] == "paused":
        resume_column, stop_column = st.columns(2)
        if resume_column.button("Resume", key="resume_timer", width="stretch"):
            timer["deadline"] = time.monotonic() + float(
                timer["remaining_seconds"]
            )
            timer["status"] = "running"
            _log_history_event(
                timer, "resumed", remaining_seconds=timer["remaining_seconds"]
            )
            st.rerun()
        if stop_column.button("Stop", key="stop_paused_timer", width="stretch"):
            _log_history_event(
                timer, "stopped", remaining_seconds=timer["remaining_seconds"]
            )
            st.session_state["timer"] = None
            st.rerun()
    elif timer["status"] == "complete":
        if st.button("Set up another timer", key="reset_timer"):
            st.session_state["timer"] = None
            st.rerun()

    _render_session_audio(timer)


st.set_page_config(page_title="Focus timer", page_icon="⏱️")
st.title("Focus timer")
st.write(
    "Choose a preset, make a custom timer, or set your Pomodoro session and break lengths."
)

timer_mode = st.selectbox(
    "Timer",
    [*PRESET_TIMERS, CUSTOM_TIMER_MODE, POMODORO_MODE],
    key="timer_mode",
)

active_timer = st.session_state.get("timer")
timer_is_active = active_timer is not None and active_timer["status"] in {
    "running",
    "paused",
    "awaiting_continue",
}
pomodoro_settings = {}
total_focus_seconds = None
custom_duration = 5
custom_time_unit = None

if timer_mode == POMODORO_MODE:
    pomodoro_plan_type = st.radio(
        "Pomodoro plan",
        ["Total focus time", "Set session lengths"],
        horizontal=True,
        key="pomodoro_plan_type",
    )
    if pomodoro_plan_type == "Total focus time":
        hours_column, minutes_column = st.columns(2)
        with hours_column:
            total_focus_hours = st.number_input(
                "Total focus time (hours)",
                min_value=0,
                max_value=168,
                value=2,
                step=1,
                key="pomodoro_total_hours",
            )
        with minutes_column:
            total_focus_minutes = st.number_input(
                "Additional minutes",
                min_value=0,
                max_value=59,
                value=0,
                step=1,
                key="pomodoro_total_minutes",
            )
        work_minutes = st.number_input(
            "Work session length (minutes)",
            min_value=1,
            max_value=1440,
            value=25,
            key="pomodoro_work_minutes",
        )
        short_break_minutes = st.number_input(
            "Short break (minutes)",
            min_value=1,
            max_value=1440,
            value=5,
            key="pomodoro_short_break_minutes",
        )
        long_break_minutes = st.number_input(
            "Long break (minutes)",
            min_value=1,
            max_value=1440,
            value=15,
            key="pomodoro_long_break_minutes",
        )
        sessions_before_long_break = st.number_input(
            "Work sessions before a long break",
            min_value=1,
            max_value=12,
            value=3,
            key="pomodoro_sessions_before_long_break",
        )
        total_focus_seconds = int(total_focus_hours * 60 + total_focus_minutes) * 60
        pomodoro_settings = {
            "work_seconds": int(work_minutes * 60),
            "short_break_seconds": int(short_break_minutes * 60),
            "long_break_seconds": int(long_break_minutes * 60),
            "sessions_before_long_break": int(sessions_before_long_break),
        }
        if total_focus_seconds == 0:
            st.warning("Enter a total focus time of at least one minute.")
        else:
            plan = _calculate_pomodoro_plan(
                total_focus_seconds,
                pomodoro_settings["work_seconds"],
                pomodoro_settings["short_break_seconds"],
                pomodoro_settings["long_break_seconds"],
                pomodoro_settings["sessions_before_long_break"],
            )
            short_break_label = (
                "short break" if plan["short_breaks"] == 1 else "short breaks"
            )
            long_break_label = (
                "long break" if plan["long_breaks"] == 1 else "long breaks"
            )
            st.info(
                f"{plan['planned_poms']} Poms (focus sessions), "
                f"{plan['short_breaks']} {short_break_label}, and "
                f"{plan['long_breaks']} {long_break_label}. "
                f"Estimated total time including breaks: "
                f"{_format_remaining_time(plan['total_duration_seconds'])}."
            )
    else:
        work_minutes = st.number_input(
            "Focused session length (minutes)",
            min_value=1,
            max_value=1440,
            value=25,
        )
        short_break_minutes = st.number_input(
            "Short break (minutes)", min_value=1, max_value=1440, value=5
        )
        long_break_minutes = st.number_input(
            "Long break (minutes)", min_value=1, max_value=1440, value=15
        )
        sessions_before_long_break = st.number_input(
            "Work sessions before a long break",
            min_value=1,
            max_value=12,
            value=3,
        )
        pomodoro_settings = {
            "work_seconds": int(work_minutes * 60),
            "short_break_seconds": int(short_break_minutes * 60),
            "long_break_seconds": int(long_break_minutes * 60),
            "sessions_before_long_break": int(sessions_before_long_break),
        }
    start_timer = st.button(
        "Start timer",
        key="start_pomodoro_timer",
        disabled=timer_is_active or total_focus_seconds == 0,
        width="stretch",
    )
else:
    with st.form("timer_settings"):
        if timer_mode == CUSTOM_TIMER_MODE:
            duration_column, unit_column = st.columns(
                2, vertical_alignment="bottom"
            )
            with duration_column:
                custom_duration = st.number_input(
                    "Custom timer duration",
                    min_value=1,
                    max_value=1440,
                    value=5,
                    step=1,
                    key="custom_timer_duration",
                )
            with unit_column:
                custom_time_unit = st.segmented_control(
                    "Time unit",
                    ["Seconds", "Minutes", "Hours"],
                    default="Minutes",
                    required=True,
                    key="custom_timer_unit",
                    width="stretch",
                )
        start_timer = st.form_submit_button(
            "Start timer",
            key="start_timer",
            disabled=timer_is_active,
            width="stretch",
        )

if start_timer:
    if timer_mode == CUSTOM_TIMER_MODE:
        if custom_time_unit is None:
            st.error("Choose a unit for your custom timer.")
        else:
            unit_seconds = {
                "Seconds": 1,
                "Minutes": 60,
                "Hours": 60 * 60,
            }[custom_time_unit]
            _start_timer(
                CUSTOM_TIMER_MODE,
                int(custom_duration * unit_seconds),
            )
    elif timer_mode == POMODORO_MODE:
        if total_focus_seconds == 0:
            st.error("Enter a total focus time of at least one minute.")
        elif total_focus_seconds is not None:
            _start_timer(
                timer_mode,
                pomodoro_settings["work_seconds"],
                **pomodoro_settings,
                total_focus_seconds=total_focus_seconds,
            )
        else:
            _start_timer(
                timer_mode,
                pomodoro_settings["work_seconds"],
                **pomodoro_settings,
            )
    else:
        _start_timer(timer_mode, PRESET_TIMERS[timer_mode])

_render_audio_tracks()
_render_timer()

with st.expander("Usage history"):
    st.caption(f"Events are appended to `{HISTORY_FILE.name}` on this computer.")
    if st.button("Refresh history", key="refresh_history"):
        st.rerun()
    if HISTORY_FILE.exists():
        with HISTORY_FILE.open("r", newline="", encoding="utf-8") as history_file:
            history = list(csv.DictReader(history_file))
        if history:
            st.table(history[-25:][::-1])
        else:
            st.info("No timer activity has been recorded yet.")
    else:
        st.info("No timer activity has been recorded yet.")
