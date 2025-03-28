"""Command-line interface for vibevoice"""

import os
import subprocess
import requests
import time
from rich import print
from rich.progress import Progress
import numpy as np
import sounddevice as sd
from scipy.io import wavfile
from datetime import datetime
import sys

from pynput.keyboard import Controller as KeyboardController, Key, Listener

from keyboard import keyboard_controller

from macros import MACROS

SERVER_HOST = "http://localhost:4242"
MIN_SAMPLES_FOR_TRANSCRIBE = 8000
VOICEKEY_DEFAULT = "shift_r"  # + CTRL
RAW_MODE = False


def start_whisper_server():
    server_script = os.path.join(os.path.dirname(__file__), "server/server.py")
    process = subprocess.Popen(["python", server_script])
    return process


def wait_for_server(timeout=1800, interval=0.5):
    global keyboard_controller
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{SERVER_HOST}/health")
            if response.status_code == 200:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(interval)

    raise TimeoutError("Server failed to start within timeout")


def process_typed(text):
    if not RAW_MODE:
        sluggified = "".join(char for char in text.lower() if char.isalnum())

        for key, value in MACROS.items():
            if sluggified == key:

                if callable(value):
                    # If the value is a callable function, execute it
                    # This allows for special keys like 'up', 'down', etc.
                    print(f"Matched '{key}' in '{sluggified}' -> Executing function")
                    value()

                    text = ""
                else:
                    print(
                        f"Matched '{key}' in '{sluggified}' -> Replacing with '{value}'"
                    )
                    # Replace the matched key with its corresponding value
                    text = value
                    break

    if text:
        keyboard_controller.type(text)


def main():
    global keyboard_controller

    RECORD_KEY = Key[VOICEKEY_DEFAULT]

    recording = False
    audio_data = []
    sample_rate = 16000

    pressed_ctrl = False
    pressed_shift = False

    progress = Progress()
    progress_current = None

    def on_press(key):
        nonlocal recording, audio_data, pressed_ctrl, pressed_shift, progress_current

        if key == Key.ctrl_r:
            pressed_ctrl = True
        if key == Key.shift_r:
            pressed_shift = True

        if pressed_ctrl and pressed_shift:
            recording = True
            audio_data = []

            if progress_current is not None:
                progress.stop()
                progress.remove_task(progress_current)

            progress.start()
            progress_current = progress.add_task(
                "[green bold]Recording...[/bold green]", total=None
            )
            progress.start_task(progress_current)

    def on_release(key):
        nonlocal recording, audio_data, pressed_shift, pressed_ctrl, progress_current

        if key == Key.ctrl_r:
            pressed_ctrl = False
        if key == Key.shift_r:
            pressed_shift = False

        if key == RECORD_KEY and (pressed_shift == False or pressed_ctrl == False):
            recording = False
            progress.stop_task(progress_current)
            progress.remove_task(progress_current)

            print("\r", end="")

            progress_current = progress.add_task(
                "[yellow bold]Transcribing...[/bold yellow]", total=None
            )

            try:
                audio_data_np = np.concatenate(audio_data, axis=0)
            except ValueError as e:
                print(e)
                return

            recording_path = os.path.abspath("recording.wav")
            audio_data_int16 = (audio_data_np * np.iinfo(np.int16).max).astype(np.int16)

            if audio_data_int16.shape[0] < MIN_SAMPLES_FOR_TRANSCRIBE:
                # Ensure there's enough data for Whisper to process
                print("[yellow]>>> (Ignoring short response.)[/yellow]")
                progress.remove_task(progress_current)
                progress.stop()
                return

            wavfile.write(recording_path, sample_rate, audio_data_int16)

            try:
                response = requests.post(
                    f"{SERVER_HOST}/transcribe",
                    json={"file_path": recording_path},
                )
                response.raise_for_status()
                transcript = response.json()["text"]

                if transcript:
                    processed_transcript = transcript  # + " "
                    print(
                        f'[yellow bold]>>>[/bold yellow] [white bold]"{processed_transcript}"[/bold white]'
                    )
                    process_typed(processed_transcript)

            except requests.exceptions.RequestException as e:
                print(f"[red]Error sending request to local API:[/red] {e}")
            except Exception as e:
                print(f"[red]Error processing transcript:[/red] {e}")
            finally:
                progress.remove_task(progress_current)
                progress_current = None
                progress.stop()

    def callback(indata, frames, time, status):
        if status:
            print(status)
        if recording:
            audio_data.append(indata.copy())

    server_process = start_whisper_server()

    try:
        print(f"[yellow]Waiting for the server to be ready...[/yellow]")
        wait_for_server()
        print(
            f"[green]Transcriber is active. Hold down CTRL+SHIFT to start dictating.[/green]"
        )
        with Listener(on_press=on_press, on_release=on_release) as listener:
            with sd.InputStream(callback=callback, channels=1, samplerate=sample_rate):
                listener.join()
    except TimeoutError as e:
        print(f"[red]Error: {e}[/red]")
        server_process.terminate()
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[yellow]Stopping...[/yellow]")
    finally:
        server_process.terminate()


if __name__ == "__main__":
    main()
