from __future__ import annotations

import re
import shutil
import subprocess


class Speaker:
    def __init__(self, enabled: bool = False, command: str | None = None):
        self.enabled = enabled
        self.command = command or _detect_command()
        self.last_text = ""

    @property
    def available(self) -> bool:
        return self.command is not None

    def say(self, text: str, remember: bool = True) -> bool:
        spoken = speech_text(text)
        if remember and spoken:
            self.last_text = spoken
        if not self.enabled or not spoken or not self.command:
            return False
        try:
            if self.command == "say":
                subprocess.Popen(["say", spoken])
            elif self.command == "espeak":
                subprocess.Popen(["espeak", spoken])
            elif self.command == "spd-say":
                subprocess.Popen(["spd-say", spoken])
            elif self.command == "powershell":
                subprocess.Popen(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        "Add-Type -AssemblyName System.Speech; "
                        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                        "$s.Speak($args[0])",
                        spoken,
                    ]
                )
            else:
                return False
        except OSError:
            return False
        return True


def speech_text(text: str) -> str:
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _looks_like_display_line(line):
            continue
        line = line.replace("`", "")
        line = line.replace("@", "you")
        line = re.sub(r"^\-\s*", "", line)
        line = re.sub(r"\s+", " ", line)
        lines.append(line)
    return ". ".join(lines)


def _detect_command() -> str | None:
    for command in ("say", "spd-say", "espeak"):
        if shutil.which(command):
            return command
    if shutil.which("powershell"):
        return "powershell"
    return None


def _looks_like_display_line(line: str) -> bool:
    if line.startswith("+") and line.endswith("+"):
        return True
    if line.startswith("|") and line.endswith("|"):
        return True
    symbol_count = sum(1 for char in line if char in ".@^v<>*=SCWGPT|-+")
    return len(line) > 12 and symbol_count / len(line) > 0.55

