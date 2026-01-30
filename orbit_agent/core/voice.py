import asyncio
import os
import tempfile
import pygame
import uuid

try:
    import edge_tts
    HAS_EDGE = True
except ImportError:
    HAS_EDGE = False

try:
    import speech_recognition as sr
    HAS_MIC = True
except ImportError:
    HAS_MIC = False
    print("[Voice] SpeechRecognition not installed.")

class VoiceEngine:
    def __init__(self):
        self.temp_dir = tempfile.gettempdir()
        self.voice = "en-US-AriaNeural" # or en-GB-SoniaNeural
        
        # Init Audio
        try:
            pygame.mixer.init()
        except:
            print("[Voice] Pygame mixer failed to init (No audio device?)")
            
        self.recognizer = sr.Recognizer() if HAS_MIC else None

    # ... (speak method unchanged) ...
    async def speak(self, text: str):
        """Synthesize and play text."""
        if not text or len(text.strip()) == 0: return
        
        if not HAS_EDGE:
            print(f"[Voice] (Text-Only Mode) {text} - Install 'edge-tts' for audio.")
            return

        print(f"[Voice] Speaking: {text[:30]}...")
        
        try:
            # Generate Unique Audio File to avoid Permission Denied
            filename = f"orbit_{uuid.uuid4().hex}.mp3"
            output_file = os.path.join(self.temp_dir, filename)
            
            communicate = edge_tts.Communicate(text, self.voice)
            await communicate.save(output_file)
            
            # Stop/Unload previous
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                try: pygame.mixer.music.unload()
                except: pass
            
            # Play Audio
            pygame.mixer.music.load(output_file)
            pygame.mixer.music.play()
                
        except Exception as e:
            print(f"[Voice] Error: {e}")

    def stop(self):
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()

    async def listen(self):
        """Listen to microphone and transcribe."""
        if not HAS_MIC: return None
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._listen_sync)

    def _listen_sync(self):
        try:
            with sr.Microphone() as source:
                print("[Voice] Listening...")
                # self.recognizer.adjust_for_ambient_noise(source) # Optional, can be slow
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=10)
                print("[Voice] Processing...")
                text = self.recognizer.recognize_google(audio)
                print(f"[Voice] Heard: {text}")
                return text
        except Exception as e:
            print(f"[Voice] Listen Error: {e}")
            return None
