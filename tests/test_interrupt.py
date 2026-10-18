"""Talking over Jarvis: speech stops, the old request is dropped, history survives."""

import threading
from types import SimpleNamespace as NS

from jarvis import brain, voice
from jarvis.config import Config


def make_brain():
    b = brain.Brain.__new__(brain.OpenAICompatibleBrain)
    brain.Brain.__init__(b)
    b._thinking_off = {}
    b.model, b.vision_model = "text-model", "vision-model"
    return b


def reply(content):
    return NS(choices=[NS(message=NS(content=content, tool_calls=None, refusal=None))])


def test_cancel_keeps_finished_turns_and_drops_the_cut_off_one():
    b = make_brain()

    class Slow:
        def __init__(self):
            self.release = threading.Event()
            self.answers = [reply("First answer."), reply("Too late.")]

        def create(self, **kwargs):
            answer = self.answers.pop(0)
            if answer.choices[0].message.content == "Too late.":
                self.release.wait(2)
            return answer

    slow = Slow()
    b.client = NS(chat=NS(completions=slow))
    assert b.run_command("first") == "First answer."
    finished = list(b._command_history)

    worker = threading.Thread(target=b.run_command, args=("second",))
    worker.start()
    b.cancel()  # the user presses the key again
    slow.release.set()
    worker.join(2)

    assert b._command_history == finished  # the interrupted turn left no trace


def test_cancel_stops_screen_control_started_before_it(monkeypatch):
    from jarvis import screen
    from jarvis.screen import Shot

    b = make_brain()
    shot = Shot(png_b64="IMG", width=100, height=100, left=0, top=0, scale=1)
    monkeypatch.setattr(screen, "capture", lambda width=None, grid=None: shot)
    monkeypatch.setattr(brain.time, "sleep", lambda s: None)
    acted = []
    monkeypatch.setattr(screen, "run_screen_tool", lambda *a: acted.append(a) or "ok")

    def scripted(**kwargs):
        b.cancel()  # interrupted while the model was choosing an action
        call = NS(id="c1", function=NS(name="click", arguments='{"x":1,"y":1,"button":"left","double":false}'))
        return NS(choices=[NS(message=NS(content="", tool_calls=[call], refusal=None))])

    b.client = NS(chat=NS(completions=NS(create=scripted)))
    assert b.operate("do something") == "Stopped."
    assert acted == []


def test_voice_stop_cuts_off_the_system_voice(monkeypatch):
    monkeypatch.setattr(voice, "config", Config(is_mac=True, voice_engine="system"))
    started = threading.Event()

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.done = threading.Event()
            started.set()

        def poll(self):
            return 0 if self.done.is_set() else None

        def terminate(self):
            self.done.set()

        def wait(self):
            self.done.wait(2)

    monkeypatch.setattr(voice.subprocess, "Popen", FakeProcess)
    talking = threading.Thread(target=voice._speak_system, args=("A long reply.",))
    talking.start()
    started.wait(1)
    voice.stop()
    talking.join(1)
    assert not talking.is_alive()


def tool_call_reply(name, args):
    import json as _json

    call = NS(id="c1", type="function", function=NS(name=name, arguments=_json.dumps(args)))
    return NS(choices=[NS(message=NS(content="", tool_calls=[call], refusal=None))])


def test_an_interrupted_command_runs_no_more_tools(monkeypatch):
    b = make_brain()
    ran = []
    monkeypatch.setattr(brain, "run_tool", lambda name, args: ran.append(name) or "ok")

    class InterruptedWhileThinking:
        def create(self, **kwargs):
            b.cancel()  # the user presses the key while the AI is thinking
            return tool_call_reply("type_text", {"text": "my address"})

    b.client = NS(chat=NS(completions=InterruptedWhileThinking()))
    generation = b.generation
    assert b.run_command("type my address", generation) == "Stopped."
    assert ran == []  # nothing was typed after the interrupt
    assert b._command_history == []


def test_interrupting_the_spoken_reply_keeps_the_finished_turn():
    b = make_brain()
    b.client = NS(chat=NS(completions=NS(create=lambda **kwargs: reply("Paris is 18 degrees."))))
    assert b.run_command("weather in Paris?") == "Paris is 18 degrees."
    finished = list(b._command_history)
    b.cancel()  # pressed while Jarvis was reading the answer out
    assert b._command_history == finished


def test_a_refusal_leaves_no_trace_so_later_commands_still_work():
    b = make_brain()
    answers = [
        NS(choices=[NS(message=NS(content="", tool_calls=None, refusal="no"))]),
        reply("Sure."),
    ]
    b.client = NS(chat=NS(completions=NS(create=lambda **kwargs: answers.pop(0))))
    assert b.run_command("something it declines") == brain.REFUSAL_REPLY
    assert b._command_history == []
    assert b.run_command("something else") == "Sure."
    assert [m["role"] for m in b._command_history] == ["user", "assistant"]


def test_long_conversations_start_fresh(monkeypatch):
    b = make_brain()
    monkeypatch.setattr(brain, "COMMAND_MEMORY_MESSAGES", 4)
    b.client = NS(chat=NS(completions=NS(create=lambda **kwargs: reply("Done."))))
    for _ in range(2):
        b.run_command("again")
    assert len(b._command_history) == 4
    b.run_command("one more")
    assert len(b._command_history) == 2


def test_the_plan_is_told_before_any_action(monkeypatch):
    import json as _json

    b = make_brain()
    events = []
    monkeypatch.setattr(brain, "run_tool", lambda name, args: events.append(("ran", name)) or "ok")
    b.announce = lambda steps: events.append(("told", steps))
    # The AI asks for an action and the plan in the same reply, plan second.
    calls = [
        NS(id="c1", type="function", function=NS(name="open_url", arguments=_json.dumps({"url": "youtube.com"}))),
        NS(id="c2", type="function", function=NS(name="announce_plan", arguments=_json.dumps({"steps": ["Open YouTube", "Search for MrBeast"]}))),
    ]
    answers = [
        NS(choices=[NS(message=NS(content="", tool_calls=calls, refusal=None))]),
        reply("Done."),
    ]
    fake = NS(calls=[])

    def create(**kwargs):
        fake.calls.append(kwargs)
        return answers.pop(0)

    b.client = NS(chat=NS(completions=NS(create=create)))
    assert b.run_command("play MrBeast") == "Done."
    assert events == [("told", ["Open YouTube", "Search for MrBeast"]), ("ran", "open_url")]
    # Tool results go back in the order the AI asked for them.
    sent = [m for m in fake.calls[1]["messages"] if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in sent] == ["c1", "c2"]


def test_interrupting_while_the_plan_is_read_out_stops_the_task(monkeypatch):
    import json as _json

    b = make_brain()
    ran = []
    monkeypatch.setattr(brain, "run_tool", lambda name, args: ran.append(name) or "ok")
    b.announce = lambda steps: b.cancel()  # the user presses the key mid-plan
    calls = [
        NS(id="c1", type="function", function=NS(name="announce_plan", arguments=_json.dumps({"steps": ["Delete the file"]}))),
        NS(id="c2", type="function", function=NS(name="press_keys", arguments=_json.dumps({"keys": "delete"}))),
    ]
    b.client = NS(chat=NS(completions=NS(create=lambda **kwargs: NS(choices=[NS(message=NS(content="", tool_calls=calls, refusal=None))]))))
    assert b.run_command("delete it", b.generation) == "Stopped."
    assert ran == []
