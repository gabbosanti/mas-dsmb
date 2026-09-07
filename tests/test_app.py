import pygame

from distributed_smb.application.dto import RenderFrame
from distributed_smb.presentation.app import GameApp


def test_run_returns_quit_when_window_closed(monkeypatch):
    monkeypatch.setattr(pygame.event, "get", lambda: [pygame.event.Event(pygame.QUIT)])

    app = GameApp(frame_handler=lambda dt, input_state: RenderFrame())
    outcome = app.run()

    assert outcome == "quit"


def test_run_returns_victory_after_overlay_duration(monkeypatch):
    monkeypatch.setattr(pygame.event, "get", lambda: [])
    monkeypatch.setattr("distributed_smb.presentation.app.VICTORY_OVERLAY_DURATION_S", 0.01)
    fake_now = iter([0.0, 0.005, 0.02])

    app = GameApp(
        frame_handler=lambda dt, input_state: RenderFrame(victory=True),
        time_provider=lambda: next(fake_now),
    )
    outcome = app.run()

    assert outcome == "victory"


def test_run_resets_victory_timer_when_victory_flag_drops(monkeypatch):
    monkeypatch.setattr("distributed_smb.presentation.app.VICTORY_OVERLAY_DURATION_S", 0.01)
    frames = iter(
        [
            RenderFrame(victory=True),
            RenderFrame(victory=False),
            RenderFrame(victory=True),
            RenderFrame(victory=True),
        ]
    )
    fake_now = iter([0.0, 0.0, 0.02])

    app = GameApp(
        frame_handler=lambda dt, input_state: next(frames),
        time_provider=lambda: next(fake_now),
    )
    outcome = app.run()

    assert outcome == "victory"
