import os
import time
import threading
import wave
import subprocess
import queue

import pyaudio
import digitalio
import pygame
import board

from dotenv import load_dotenv
from openai import OpenAI

# ============================================================
# CONFIGURATION
# ============================================================

ENGAGEMENT_PATH = "/media/davidcr/C061-9EFD1"
CLIENT_FILE = os.path.join(ENGAGEMENT_PATH, "CLIENT.txt")
PROPOSAL_FILE = os.path.join(ENGAGEMENT_PATH, "ENGAGEMENT_PROPOSAL.txt")

CHAT_MODEL = "gpt-5-mini"
TTS_MODEL = "gpt-4o-mini-tts"
VOICE_NAME = "echo"

BUTTON_PIN = board.D22

# ============================================================
# LOAD ENV + OPENAI
# ============================================================

load_dotenv()
client = OpenAI()

# ============================================================
# BUTTON SETUP
# ============================================================

listen_button = digitalio.DigitalInOut(BUTTON_PIN)
listen_button.direction = digitalio.Direction.INPUT
listen_button.pull = digitalio.Pull.UP

def button_pressed():
    return not listen_button.value

# ============================================================
# FILE LOADING
# ============================================================

def read_text_file(path):
    for enc in ("utf-8", "cp1252", "mac_roman"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read().strip()
        except UnicodeDecodeError:
            continue
    raise Exception("Could not decode file")

def load_engagement_files():
    return read_text_file(CLIENT_FILE), read_text_file(PROPOSAL_FILE)

# ============================================================
# UI
# ============================================================

class TouchUI:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        pygame.mouse.set_visible(False)
        self.w, self.h = self.screen.get_size()
        self.font = pygame.font.SysFont(None, 40)
        self.bg = (0, 0, 0)
        self.fg = (255, 255, 255)
        self.msgq = queue.SimpleQueue()
        self.running = True

    def post(self, text):
        self.msgq.put(text)

    def loop(self):
        clock = pygame.time.Clock()
        text = ""

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False

            while not self.msgq.empty():
                text = self.msgq.get()

            self.screen.fill(self.bg)

            lines = []
            words = text.split()
            line = ""
            for w in words:
                test = line + " " + w
                if self.font.size(test)[0] < self.w - 40:
                    line = test
                else:
                    lines.append(line)
                    line = w
            if line:
                lines.append(line)

            y = 50
            for l in lines[:15]:
                surf = self.font.render(l, True, self.fg)
                self.screen.blit(surf, (20, y))
                y += 45

            pygame.display.flip()
            clock.tick(30)

        pygame.quit()

# ============================================================
# AUDIO
# ============================================================

def record_audio(filename="input.wav"):
    audio = pyaudio.PyAudio()
    stream = audio.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=48000,
        input=True,
        frames_per_buffer=1024
    )

    frames = []

    # Wait for press
    while not button_pressed():
        time.sleep(0.01)

    # Record while held
    while button_pressed():
        data = stream.read(1024, exception_on_overflow=False)
        frames.append(data)

    stream.stop_stream()
    stream.close()
    audio.terminate()

    if not frames:
        return None

    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(b''.join(frames))

    return filename

def transcribe(filename):
    with open(filename, "rb") as audio_file:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file
        )
    return result.text.strip()

def speak(text):
    mp3_path = "speech.mp3"
    wav_path = "speech.wav"

    with client.audio.speech.with_streaming_response.create(
        model=TTS_MODEL,
        voice=VOICE_NAME,
        input=text
    ) as response:
        response.stream_to_file(mp3_path)

    subprocess.run(["ffmpeg", "-y", "-i", mp3_path, "-ar", "48000", wav_path],
                   stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)

    subprocess.run(["paplay", wav_path])

    os.remove(mp3_path)
    os.remove(wav_path)

# ============================================================
# MAIN
# ============================================================

def main():
    client_text, proposal_text = load_engagement_files()

    ui = TouchUI()

    conversation = []

    def worker():

        intro = "Thank you for this proposal. I like it a lot, but I have many questions."
        ui.post(intro)
        speak(intro)

        summary_prompt = f"""
You are a thoughtful executive reviewing a proposal.

Summarize the following proposal in 3 to 5 concise sentences.

Proposal:
{proposal_text}
"""

        summary = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": summary_prompt}]
        ).choices[0].message.content.strip()

        ui.post(summary)
        speak(summary)

        system_prompt = f"""
You are a friendly but rigorous executive.

Ask short, clear, high-level questions about the proposal.
Ask exactly one question at a time.
End every response with a single question.
Do not summarize.
Do not provide feedback yet.
Wait for the user's answer before asking another question.
"""

        conversation.append({"role": "system", "content": system_prompt})

        # First question
        conversation.append({"role": "user", "content": "Please ask your first high-level question."})

        first_question = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=conversation
        ).choices[0].message.content.strip()

        conversation.append({"role": "assistant", "content": first_question})
        ui.post(first_question)
        speak(first_question)

        while True:

            audio_file = record_audio()
            if not audio_file:
                continue

            user_text = transcribe(audio_file)
            os.remove(audio_file)

            if not user_text:
                continue

            if user_text.lower().strip() == "end interview":
                speak("Thank you. This concludes our discussion.")
                ui.post("Interview ended.")
                break

            conversation.append({"role": "user", "content": user_text})

            next_question = client.chat.completions.create(
                model=CHAT_MODEL,
                messages=conversation
            ).choices[0].message.content.strip()

            conversation.append({"role": "assistant", "content": next_question})
            ui.post(next_question)
            speak(next_question)

        ui.running = False

    threading.Thread(target=worker, daemon=True).start()
    ui.loop()

if __name__ == "__main__":
    main()