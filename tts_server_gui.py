from http.server import HTTPServer, BaseHTTPRequestHandler
import socket
import win32com.client
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime
import os
import threading
from threading import Event, Lock, Condition
from typing import Any, Optional
import select
import ipaddress
import string
import errno
import time
import traceback
import json
import queue
import sys



"""
TTS Server - Low Latency

INSTALLATION:
    To install Python packages, use:
        py -m pip install <package_name>
    
    Required dependencies:
        py -m pip install pywin32

Features:
1. Async TTS mode (eliminates post-speech silence)
2. Realtek speaker prioritized (lowest latency)
3. Voice gender matching (male/female/default)
4. Accurate receive timestamps
5. Event-driven logging (minimal CPU, instant display)
6. Adjustable speech speed (0-3: Normal to Very Fast)
7. Adjustable volume (0-100%)
8. Single instance enforcement (only one server at a time)
9. One log file per day (appends to same file if restarted)
10. Optional file logging (disabled by default)
11. Log window shows last 60 messages (~1 minute)
"""

class OptimizedQueue:
    """Ultra-low latency queue with condition variable and size limit"""
    def __init__(self, maxsize=100):
        self._queue = []
        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._maxsize = maxsize
        
    def put(self, item: Any) -> bool:
        """Add item and wake waiting thread instantly. Returns False if queue is full."""
        with self._condition:
            if self._maxsize > 0 and len(self._queue) >= self._maxsize:
                return False  # Queue full, reject message
            self._queue.append(item)
            self._condition.notify()
            return True
            
    def get_wait(self, timeout: Optional[float] = None) -> Optional[Any]:
        """Block until item available - instant wake when item added"""
        with self._condition:
            while len(self._queue) == 0:
                if not self._condition.wait(timeout):
                    return None
            return self._queue.pop(0)
    
    def qsize(self) -> int:
        """Get queue size"""
        with self._lock:
            return len(self._queue)


class TTSRequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/tts':
            # TIMESTAMP when HTTP request arrives
            receive_timestamp = datetime.now()
            
            content_length = int(self.headers['Content-Length'])
            message = self.rfile.read(content_length).decode('utf-8')
            
            # Try to parse as JSON
            try:
                message_data = json.loads(message)
                text = message_data.get("text", "")
                voice_gender = message_data.get("voice_gender", "default")
            except (json.JSONDecodeError, TypeError):
                # Not JSON, treat as plain text
                text = message
                voice_gender = "default"
            
            cleaned_message = self.server.gui.clean_text(text)
            
            if cleaned_message:
                if not self.server.message_queue.put((cleaned_message, "HTTP", voice_gender, receive_timestamp)):
                    self.send_response(503)  # Service Unavailable
                    self.end_headers()
                    self.wfile.write(b"Queue full")
                    return
                
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Empty message")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


class TTSServerGUI:
    def __init__(self):
        try:
            # Single instance check - MUST BE FIRST
            self.lock_file_path = "tts_server.lock"
            if not self.acquire_lock():
                print("ERROR: Another instance of TTS Server is already running!")
                print(f"Lock file: {os.path.abspath(self.lock_file_path)}")
                
                # Try to show error dialog if possible
                try:
                    root = tk.Tk()
                    root.withdraw()
                    messagebox.showerror(
                        "Server Already Running",
                        "Another instance of TTS Server is already running.\n\n"
                        "Only one server instance can run at a time.\n\n"
                        f"Lock file: {os.path.abspath(self.lock_file_path)}"
                    )
                    root.destroy()
                except:
                    pass
                
                sys.exit(1)
            
            self.root = tk.Tk()
            self.root.title("TTS Server - Low Latency")
            self.root.geometry("800x700")
            
            self.message_queue = OptimizedQueue()
            self.shutdown_event = Event()
            self.restart_tts_event = Event()
            
            # Log buffer for event-driven display
            self.log_buffer = []
            self.log_buffer_lock = Lock()
            
            # Log file writer queue
            self.log_file_queue = queue.Queue()
            
            # File logging enabled flag (disabled by default)
            self.enable_file_logging = tk.BooleanVar(value=False)
            
            # Setup logging
            self.log_directory = "server_logs"
            if not os.path.exists(self.log_directory):
                os.makedirs(self.log_directory)
            
            # Use date-based file names (one per day)
            today = datetime.now().strftime('%Y%m%d')
            self.system_log_file = os.path.join(
                self.log_directory, 
                f"server_system_log_{today}.txt"
            )
            self.message_log_file = os.path.join(
                self.log_directory, 
                f"server_messages_log_{today}.txt"
            )
            
            print(f"System log: {self.system_log_file}")
            print(f"Message log: {self.message_log_file}")
            print(f"File logging: Disabled by default")
            
            # Server configuration
            self.tcp_port = 5000
            self.http_port = 5001
            
            # Allowed IP ranges
            self.allowed_networks = [
                ipaddress.ip_network('127.0.0.0/8'),
                ipaddress.ip_network('192.168.0.0/16'),
                ipaddress.ip_network('10.0.0.0/8'),
                ipaddress.ip_network('172.16.0.0/12')
            ]
            
            # Statistics
            self.message_counter = 0
            self.processed_counter = 0
            self.stats_lock = Lock()
            
            # Audio device info
            self.audio_devices = []
            self.selected_audio_index = tk.IntVar(value=0)
            self.computer_speaker_index = None
            
            # Voice info (with gender classification)
            self.voices = []  # List of (index, name, gender)
            self.male_voices = []  # Indices of male voices
            self.female_voices = []  # Indices of female voices
            self.selected_voice_index = tk.IntVar(value=0)
            
            # Volume control
            self.volume = tk.IntVar(value=100)
            
            # Speed/Rate control (Range: 0 to 3)
            self.speech_rate = tk.IntVar(value=0)
            
            # Detect audio devices and voices
            self.detect_audio_devices()
            self.detect_voices()
            
            # Setup GUI
            self.setup_gui()
            
            # Server attributes
            self.tcp_socket = None
            self.httpd = None
            
            # Start components
            self.start_log_file_writer()
            self.start_message_processor()
            self.start_servers()
            
            self.root.protocol("WM_DELETE_WINDOW", self.on_close)
            
            print("Initialization complete!")
            
        except Exception as e:
            print(f"ERROR during initialization: {e}")
            traceback.print_exc()
            # Release lock on error
            if hasattr(self, 'lock_file_path'):
                self.release_lock()
            raise

    def acquire_lock(self) -> bool:
        """Acquire lock file to ensure single instance"""
        try:
            # Try to create lock file exclusively
            if os.path.exists(self.lock_file_path):
                # Check if lock file is stale (process not running)
                try:
                    with open(self.lock_file_path, 'r') as f:
                        pid = int(f.read().strip())
                    
                    # Check if process is still running
                    if self.is_process_running(pid):
                        return False  # Another instance is running
                    else:
                        # Stale lock file, remove it
                        print(f"Removing stale lock file (PID {pid} not running)")
                        os.remove(self.lock_file_path)
                except:
                    # Invalid lock file, remove it
                    print("Removing invalid lock file")
                    os.remove(self.lock_file_path)
            
            # Create lock file with current PID
            with open(self.lock_file_path, 'w') as f:
                f.write(str(os.getpid()))
            
            print(f"Lock acquired (PID: {os.getpid()})")
            return True
        except Exception as e:
            print(f"Error acquiring lock: {e}")
            return False

    def is_process_running(self, pid: int) -> bool:
        """Check if a process with given PID is running"""
        try:
            # Try psutil first (more reliable)
            import psutil
            return psutil.pid_exists(pid)
        except ImportError:
            # psutil not available, use Windows-specific check
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                PROCESS_QUERY_INFORMATION = 0x0400
                handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            except:
                # Fallback: assume process is running if we can't check
                return True

    def release_lock(self):
        """Release lock file"""
        try:
            if os.path.exists(self.lock_file_path):
                os.remove(self.lock_file_path)
                print(f"Lock released (PID: {os.getpid()})")
        except Exception as e:
            print(f"Error releasing lock: {e}")

    def detect_voices(self):
        """Detect all available TTS voices and classify by gender"""
        try:
            import pythoncom
            pythoncom.CoInitialize()
            
            try:
                engine = win32com.client.Dispatch("SAPI.SpVoice")
                voices = engine.GetVoices()
                
                self.voices = []
                self.male_voices = []
                self.female_voices = []
                zira_index = None
                
                # Male voice keywords
                male_keywords = ["david", "mark", "james", "paul", "male", "man"]
                # Female voice keywords  
                female_keywords = ["zira", "hazel", "susan", "female", "woman", "huihui", "hanhan"]
                
                for i in range(voices.Count):
                    try:  # Wrap each voice query in try-except
                        voice = voices.Item(i)
                        name = voice.GetDescription()
                        name_lower = name.lower()
                        
                        # Classify gender based on name
                        gender = "unknown"
                        if any(kw in name_lower for kw in male_keywords):
                            gender = "male"
                            self.male_voices.append(i)
                        elif any(kw in name_lower for kw in female_keywords):
                            gender = "female"
                            self.female_voices.append(i)
                        
                        self.voices.append((i, name, gender))
                        print(f"Voice [{i}]: {name} ({gender})")
                        
                        if "zira" in name_lower and zira_index is None:
                            zira_index = i
                            
                    except Exception:
                        # Silently skip voices that cause exceptions
                        pass
                
                # Set default voice (prefer Zira/female, then first available)
                if zira_index is not None:
                    self.selected_voice_index.set(zira_index)
                    print(f"Default voice: Zira (index {zira_index})")
                elif self.voices:
                    self.selected_voice_index.set(0)
                    print(f"Default voice: {self.voices[0][1]}")
                
                print(f"Male voices: {len(self.male_voices)}")
                print(f"Female voices: {len(self.female_voices)}")
                        
            except Exception as e:
                print(f"Error detecting voices: {e}")
                self.voices = [(0, "Default Voice", "unknown")]
            finally:
                pythoncom.CoUninitialize()
                
        except Exception as e:
            print(f"Error in detect_voices: {e}")
            self.voices = [(0, "Default Voice", "unknown")]
        
    def get_voice_by_gender(self, requested_gender: str) -> int:
        """Get voice index matching requested gender, fallback to default"""
        if requested_gender == "male" and self.male_voices:
            return self.male_voices[0]  # First male voice
        elif requested_gender == "female" and self.female_voices:
            return self.female_voices[0]  # First female voice
        else:
            # Return server default
            return self.selected_voice_index.get()

    def detect_audio_devices(self):
        """Detect and prioritize audio output devices - FORCE REALTEK SPEAKER"""
        try:
            import pythoncom
            pythoncom.CoInitialize()
            
            try:
                engine = win32com.client.Dispatch("SAPI.SpVoice")
                outputs = engine.GetAudioOutputs()
                
                all_devices = []
                for i in range(outputs.Count):
                    desc = outputs.Item(i).GetDescription()
                    all_devices.append((i, desc))
                    print(f"Detected audio device [{i}]: {desc}")
                
                # Find Realtek speaker (laptop speaker) - HIGHEST PRIORITY
                realtek_speaker = None
                computer_speaker = None
                alternative_speakers = []
                other_devices = []
                
                for idx, desc in all_devices:
                    desc_lower = desc.lower()
                    
                    # PRIORITY 1: Realtek Speakers (laptop speaker - lowest latency)
                    if "realtek" in desc_lower and "speaker" in desc_lower:
                        realtek_speaker = (idx, desc, "recommended")
                        print(f"★ FOUND REALTEK SPEAKER (Best latency): [{idx}] {desc}")
                        continue
                    
                    # PRIORITY 2: Other computer speakers
                    is_monitor = any(kw in desc_lower for kw in ["monitor", "display", "dell", "lg", "samsung", "hdmi", "asus", "acer", "hp"])
                    is_computer_speaker = any(kw in desc_lower for kw in ["speakers", "internal", "built-in", "conexant", "idt", "laptop"])
                    has_speaker = "speaker" in desc_lower
                    
                    if is_computer_speaker and not is_monitor and computer_speaker is None:
                        computer_speaker = (idx, desc, "recommended")
                    elif has_speaker and not is_monitor:
                        alternative_speakers.append((idx, desc, "alternative"))
                    else:
                        other_devices.append((idx, desc, "other"))
                
                # Build prioritized list - REALTEK FIRST
                self.audio_devices = []
                
                if realtek_speaker:
                    self.audio_devices.append(realtek_speaker)
                    self.computer_speaker_index = realtek_speaker[0]
                elif computer_speaker:
                    self.audio_devices.append(computer_speaker)
                    self.computer_speaker_index = computer_speaker[0]
                
                alternative_speakers.sort(key=lambda x: x[1])
                self.audio_devices.extend(alternative_speakers)
                
                other_devices.sort(key=lambda x: x[1])
                self.audio_devices.extend(other_devices)
                
                # Fallback
                if self.computer_speaker_index is None and self.audio_devices:
                    self.computer_speaker_index = self.audio_devices[0][0]
                    self.audio_devices[0] = (self.audio_devices[0][0], self.audio_devices[0][1], "recommended")
                
                if self.audio_devices:
                    self.selected_audio_index.set(self.audio_devices[0][0])
                    print(f"Selected default audio: [{self.audio_devices[0][0]}] {self.audio_devices[0][1]}")
                        
            except Exception as e:
                print(f"Error detecting audio devices: {e}")
                self.audio_devices = [(0, "Default Audio Device", "recommended")]
                self.computer_speaker_index = 0
            finally:
                pythoncom.CoUninitialize()
                
        except Exception as e:
            print(f"Error in detect_audio_devices: {e}")
            self.audio_devices = [(0, "Default Audio Device", "recommended")]
            self.computer_speaker_index = 0

    def is_computer_speaker(self, index: int) -> bool:
        """Check if the given index is the computer speaker"""
        return index == self.computer_speaker_index

    def get_next_message_id(self) -> int:
        with self.stats_lock:
            self.message_counter += 1
            return self.message_counter

    def clean_text(self, text: str) -> str:
        allowed_chars = set(string.printable)
        allowed_chars -= set("\x0b\x0c")
        cleaned = "".join(c for c in text if c in allowed_chars)
        return cleaned.strip()

    def setup_gui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Server status
        status_frame = ttk.Frame(main_frame)
        status_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.tcp_status_var = tk.StringVar(value=f"TCP:{self.tcp_port} Starting")
        ttk.Label(status_frame, textvariable=self.tcp_status_var).grid(row=0, column=0, padx=(0, 10))
        
        self.http_status_var = tk.StringVar(value=f"HTTP:{self.http_port} Starting")
        ttk.Label(status_frame, textvariable=self.http_status_var).grid(row=0, column=1)
        
        # Audio device selection
        audio_frame = ttk.LabelFrame(main_frame, text="Audio Output", padding="5")
        audio_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        ttk.Label(audio_frame, text="Device:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        
        self.audio_combo = ttk.Combobox(audio_frame, state="readonly", width=60)
        self.audio_combo.grid(row=0, column=1, sticky=(tk.W, tk.E))
        
        if self.audio_devices:
            device_names = []
            for idx, desc, category in self.audio_devices:
                if category == "recommended":
                    device_names.append(f"✓ {desc} [Recommended - Low Latency]")
                elif category == "alternative":
                    device_names.append(f"  {desc} [Alternative Speaker]")
                else:
                    device_names.append(f"  {desc}")
            
            self.audio_combo['values'] = device_names
            self.audio_combo.current(0)
        
        # Auto-apply on change
        self.audio_combo.bind('<<ComboboxSelected>>', lambda e: self.on_audio_device_changed())
        
        self.warning_label = ttk.Label(
            audio_frame, 
            text="⚠ Warning: Bluetooth/USB devices may have higher latency!",
            foreground="orange"
        )
        
        audio_frame.columnconfigure(1, weight=1)
        
        # Voice selection
        voice_frame = ttk.LabelFrame(main_frame, text="Default Voice (Server)", padding="5")
        voice_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        ttk.Label(voice_frame, text="Voice:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        
        self.voice_combo = ttk.Combobox(voice_frame, state="readonly", width=60)
        self.voice_combo.grid(row=0, column=1, sticky=(tk.W, tk.E))
        
        if self.voices:
            voice_names = [f"{name} ({gender})" for idx, name, gender in self.voices]
            self.voice_combo['values'] = voice_names
            
            selected_idx = self.selected_voice_index.get()
            if selected_idx < len(voice_names):
                self.voice_combo.current(selected_idx)
        
        # Auto-apply on change
        self.voice_combo.bind('<<ComboboxSelected>>', lambda e: self.on_voice_changed())
        
        # Voice info
        voice_info = ttk.Label(
            voice_frame,
            text="Client can request male/female voice, or use this default",
            foreground="gray"
        )
        voice_info.grid(row=1, column=0, columnspan=2, pady=(5, 0))
        
        voice_frame.columnconfigure(1, weight=1)
        
        # Speed control
        speed_frame = ttk.LabelFrame(main_frame, text="Speech Speed", padding="5")
        speed_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # Speed radio buttons in a horizontal layout
        speed_radio_frame = ttk.Frame(speed_frame)
        speed_radio_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))
        
        ttk.Label(speed_radio_frame, text="Speed:").pack(side=tk.LEFT, padx=(0, 10))
        
        speed_options = [
            ("Normal (1.0x)", 0),
            ("Faster (1.2x)", 1),
            ("Fast (1.4x)", 2),
            ("Very Fast (1.6x)", 3)
        ]
        
        for label, value in speed_options:
            ttk.Radiobutton(
                speed_radio_frame, 
                text=label, 
                variable=self.speech_rate, 
                value=value,
                command=self.on_speed_changed  # Auto-apply on click
            ).pack(side=tk.LEFT, padx=10)
        
        speed_frame.columnconfigure(0, weight=1)
        
        # Volume control
        volume_frame = ttk.LabelFrame(main_frame, text="Volume", padding="5")
        volume_frame.grid(row=4, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        ttk.Label(volume_frame, text="Level:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        
        self.volume_scale = ttk.Scale(
            volume_frame, 
            from_=0, 
            to=100, 
            orient=tk.HORIZONTAL,
            variable=self.volume,
            command=self.on_volume_slider_changed
        )
        self.volume_scale.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(0, 5))
        
        self.volume_label = ttk.Label(volume_frame, text="100%")
        self.volume_label.grid(row=0, column=2)
        
        # Auto-apply when slider released
        self.volume_scale.bind('<ButtonRelease-1>', lambda e: self.on_volume_applied())
        
        # Volume note
        volume_note = ttk.Label(
            volume_frame, 
            text="Tip: You can also use keyboard volume keys to control speaker volume",
            foreground="gray"
        )
        volume_note.grid(row=1, column=0, columnspan=3, pady=(5, 0))
        
        volume_frame.columnconfigure(1, weight=1)
        
        # Logging control
        logging_frame = ttk.LabelFrame(main_frame, text="Logging", padding="5")
        logging_frame.grid(row=5, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.log_checkbox = ttk.Checkbutton(
            logging_frame,
            text="Save logs to files (server_logs/*.txt)",
            variable=self.enable_file_logging,
            command=self.on_logging_toggled
        )
        self.log_checkbox.grid(row=0, column=0, sticky=tk.W)
        
        logging_frame.columnconfigure(0, weight=1)
        
        # Statistics
        stats_frame = ttk.Frame(main_frame)
        stats_frame.grid(row=6, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 5))
        
        self.queue_label = ttk.Label(stats_frame, text="Queue: 0")
        self.queue_label.grid(row=0, column=0, padx=(0, 20))
        
        self.processed_label = ttk.Label(stats_frame, text="Processed: 0")
        self.processed_label.grid(row=0, column=1)
        
        # Log
        ttk.Label(main_frame, text="Log (last 60 messages):").grid(row=7, column=0, sticky=tk.W)
        self.log_text = scrolledtext.ScrolledText(main_frame, height=10)
        self.log_text.grid(row=8, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(8, weight=1)
        
        self.update_stats_display()

    def on_logging_toggled(self):
        """Handle logging checkbox toggle"""
        if self.enable_file_logging.get():
            self.log_system_async("File logging enabled")
            # Add startup marker to log files
            startup_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            startup_marker = f"\n{'='*80}\nLogging enabled: {startup_time}\n{'='*80}\n"
            for log_file in [self.system_log_file, self.message_log_file]:
                try:
                    with open(log_file, 'a', encoding='utf-8') as f:
                        f.write(startup_marker)
                except:
                    pass
        else:
            self.log_system_async("File logging disabled")

    def on_volume_slider_changed(self, value):
        """Update volume label as slider moves"""
        volume_value = int(float(value))
        self.volume_label.config(text=f"{volume_value}%")

    def on_volume_applied(self):
        """Apply volume change when slider released"""
        volume_value = self.volume.get()
        self.log_system_async(f"Volume changed to {volume_value}%")
        self.restart_tts_event.set()

    def on_speed_changed(self):
        """Apply speed change (auto-applied on radio button click)"""
        speed_value = self.speech_rate.get()
        speed_labels = {0: "Normal", 1: "Faster", 2: "Fast", 3: "Very Fast"}
        self.log_system_async(f"Speech rate: {speed_labels.get(speed_value, speed_value)}")
        self.restart_tts_event.set()

    def on_voice_changed(self):
        """Handle voice selection change (auto-applied)"""
        selected_pos = self.voice_combo.current()
        if selected_pos >= 0:
            actual_index, voice_name, gender = self.voices[selected_pos]
            self.selected_voice_index.set(actual_index)
            self.log_system_async(f"Default voice: {voice_name} ({gender})")
            self.restart_tts_event.set()

    def on_audio_device_changed(self):
        """Handle audio device selection change (auto-applied)"""
        selected_pos = self.audio_combo.current()
        if selected_pos >= 0:
            actual_index, device_name, category = self.audio_devices[selected_pos]
            
            # Check if selecting non-recommended device
            if not self.is_computer_speaker(actual_index):
                # Check if it's Bluetooth or USB (high latency devices)
                desc_lower = device_name.lower()
                is_high_latency = any(kw in desc_lower for kw in ["bluetooth", "usb", "wireless", "headphone", "headset"])
                
                if is_high_latency:
                    response = messagebox.showwarning(
                        "Audio Device Warning",
                        f"You are selecting a Bluetooth/USB device:\n\n{device_name}\n\n"
                        "Bluetooth and USB audio devices typically have 500-2000ms latency due to:\n"
                        "- Wireless transmission delay\n"
                        "- Audio buffering\n"
                        "- Post-speech silence padding\n\n"
                        "For best performance, use the laptop's built-in speaker (Realtek).\n\n"
                        "Do you want to continue with this selection?",
                        type=messagebox.OKCANCEL
                    )
                else:
                    response = messagebox.showwarning(
                        "Audio Device Warning",
                        f"You are selecting:\n\n{device_name}\n\n"
                        "This may have higher latency than the laptop speaker.\n\n"
                        "Do you want to continue?",
                        type=messagebox.OKCANCEL
                    )
                
                if response == 'cancel':
                    self.audio_combo.current(0)
                    return
                
                self.warning_label.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(5, 0))
            else:
                self.warning_label.grid_forget()
            
            self.selected_audio_index.set(actual_index)
            self.log_system_async(f"Audio device: {device_name}")
            self.restart_tts_event.set()

    def update_stats_display(self):
        try:
            queue_size = self.message_queue.qsize()
            self.queue_label.config(text=f"Queue: {queue_size}")
            
            with self.stats_lock:
                processed = self.processed_counter
            
            self.processed_label.config(text=f"Processed: {processed}")
        except:
            pass
        
        try:
            self.root.after(100, self.update_stats_display)
        except:
            pass

    def log_message_async(self, message: str, timestamp: datetime = None):
        """Log a TTS message"""
        now = timestamp if timestamp else datetime.now()
        screen_time = now.strftime("%H:%M:%S.%f")[:-3]
        file_timestamp = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        screen_entry = f"[{screen_time}] {message}"
        file_entry = f"[{file_timestamp}] {message}"
        
        # Add to buffer
        with self.log_buffer_lock:
            self.log_buffer.append(('message', screen_entry, file_entry, self.message_log_file))
        
        # Trigger display update
        self._trigger_log_display()

    def log_system_async(self, message: str):
        """Log a system event"""
        now = datetime.now()
        screen_time = now.strftime("%H:%M:%S.%f")[:-3]
        file_timestamp = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        screen_entry = f"[{screen_time}] {message}"
        file_entry = f"[{file_timestamp}] {message}"
        
        # Add to buffer
        with self.log_buffer_lock:
            self.log_buffer.append(('system', screen_entry, file_entry, self.system_log_file))
        
        # Trigger display update
        self._trigger_log_display()

    def _trigger_log_display(self):
        """Trigger log display"""
        self._display_pending_logs()

    def _display_pending_logs(self):
        """Display all pending logs on GUI thread"""
        # Get all pending entries
        with self.log_buffer_lock:
            if not self.log_buffer:
                return
            entries = self.log_buffer[:]
            self.log_buffer.clear()
        
        # Schedule GUI update
        def update_gui():
            # Insert text
            screen_text = "\n".join([e[1] for e in entries]) + "\n"
            self.log_text.insert(tk.END, screen_text)
            
            # Limit to 60 lines
            all_content = self.log_text.get('1.0', 'end-1c')
            lines = all_content.split('\n')
            
            if len(lines) > 60:
                self.log_text.delete('1.0', 'end')
                self.log_text.insert('1.0', '\n'.join(lines[-60:]))
            
            self.log_text.see(tk.END)
        
        # Execute on main thread
        try:
            self.root.after(0, update_gui)
        except:
            pass
        
        # Queue for file writer
        if self.enable_file_logging.get():
            self.log_file_queue.put(entries)

    def start_log_file_writer(self):
        """Single dedicated thread for writing log files"""
        def write_logs():
            while not self.shutdown_event.is_set():
                try:
                    entries = self.log_file_queue.get(timeout=1.0)
                    if entries:
                        self._write_logs_to_files(entries)
                except queue.Empty:
                    continue
        
        self.log_writer_thread = threading.Thread(target=write_logs, daemon=True, name="LogWriter")
        self.log_writer_thread.start()

    def _write_logs_to_files(self, entries):
        """Write logs to files asynchronously with buffering"""
        # Only write if file logging is enabled
        if not self.enable_file_logging.get():
            return
        
        file_writes = {}
        for log_type, screen_entry, file_entry, log_file in entries:
            if log_file not in file_writes:
                file_writes[log_file] = []
            file_writes[log_file].append(file_entry)
        
        for log_file, lines in file_writes.items():
            try:
                # Use larger buffer to reduce disk I/O
                with open(log_file, 'a', encoding='utf-8', buffering=8192) as f:
                    f.write("\n".join(lines) + "\n")
            except:
                pass

    def update_status(self, server_type: str, status: str):
        def do_update():
            status_var = self.tcp_status_var if server_type == "TCP" else self.http_status_var
            port = self.tcp_port if server_type == "TCP" else self.http_port
            status_var.set(f"{server_type}:{port} {status}")
        
        try:
            self.root.after(0, do_update)
            self.log_system_async(f"{server_type} {status}")
        except:
            pass

    def start_message_processor(self):
        """Start the TTS message processor with voice gender support"""
        def process_messages():
            try:
                import pythoncom
                
                while not self.shutdown_event.is_set():
                    pythoncom.CoInitialize()
                    self.restart_tts_event.clear()
                    
                    self.log_system_async("TTS started (async mode)")
                    
                    try:
                        engine = win32com.client.Dispatch("SAPI.SpVoice")
                        
                        # Set audio output
                        selected_audio_index = self.selected_audio_index.get()
                        outputs = engine.GetAudioOutputs()
                        
                        if selected_audio_index < outputs.Count:
                            engine.AudioOutput = outputs.Item(selected_audio_index)
                            device_name = outputs.Item(selected_audio_index).GetDescription()
                            self.log_system_async(f"Output: {device_name}")
                        
                        # Get all voices for switching
                        all_voices = engine.GetVoices()
                        
                        # Set default voice
                        selected_voice_index = self.selected_voice_index.get()
                        
                        if selected_voice_index < all_voices.Count:
                            engine.Voice = all_voices.Item(selected_voice_index)
                            voice_name = all_voices.Item(selected_voice_index).GetDescription()
                            self.log_system_async(f"Default voice: {voice_name}")
                        
                        # Set volume and rate
                        engine.Volume = self.volume.get()
                        engine.Rate = self.speech_rate.get()
                        
                        self.log_system_async(f"Volume: {self.volume.get()}%")
                        
                        rate_value = self.speech_rate.get()
                        rate_labels = {0: "Normal", 1: "Faster", 2: "Fast", 3: "Very Fast"}
                        self.log_system_async(f"Speed: {rate_labels.get(rate_value, rate_value)}")
                        
                        self.log_system_async("Ready")
                        
                    except Exception as e:
                        self.log_system_async(f"Init error: {e}")
                        traceback.print_exc()
                        pythoncom.CoUninitialize()
                        time.sleep(1)
                        continue
                    
                    # Main processing loop
                    while not self.shutdown_event.is_set() and not self.restart_tts_event.is_set():
                        try:
                            # OPTIMIZED: 1ms timeout
                            message = self.message_queue.get_wait(timeout=0.001)
                            
                            if message:
                                # Unpack message with receive timestamp
                                text, source, voice_gender, receive_timestamp = message
                                msg_id = self.get_next_message_id()
                                
                                # Log immediately when message is received
                                self.log_message_async(f"[#{msg_id}] {text}", receive_timestamp)
                                
                                # Select voice based on client preference
                                if voice_gender in ["male", "female"]:
                                    requested_voice_idx = self.get_voice_by_gender(voice_gender)
                                    
                                    if requested_voice_idx < all_voices.Count:
                                        engine.Voice = all_voices.Item(requested_voice_idx)
                                else:
                                    # Use server default voice
                                    default_voice_idx = self.selected_voice_index.get()
                                    if default_voice_idx < all_voices.Count:
                                        engine.Voice = all_voices.Item(default_voice_idx)
                                
                                # Update volume and rate dynamically
                                engine.Volume = self.volume.get()
                                engine.Rate = self.speech_rate.get()
                                
                                # ASYNC MODE - Returns immediately
                                engine.Speak(text, 1)  # 1 = async
                                
                                # Wait ONLY until speech completes
                                while engine.Status.RunningState == 2:  # 2 = speaking
                                    time.sleep(0.001)  # 1ms polling
                                
                                with self.stats_lock:
                                    self.processed_counter += 1
                        
                        except Exception as e:
                            self.log_system_async(f"Error: {e}")
                            traceback.print_exc()
                    
                    pythoncom.CoUninitialize()
                    
                    if self.restart_tts_event.is_set():
                        self.log_system_async("Restarting...")
                        time.sleep(0.3)
                
                self.log_system_async("TTS stopped")
                
            except Exception as e:
                print(f"Processor error: {e}")
                traceback.print_exc()
        
        self.processor_thread = threading.Thread(
            target=process_messages,
            daemon=True,
            name="TTS-Processor"
        )
        self.processor_thread.start()

    def is_ip_allowed(self, ip: str) -> bool:
        try:
            ip_addr = ipaddress.ip_address(ip)
            return any(ip_addr in net for net in self.allowed_networks)
        except ValueError:
            return False

    def handle_tcp_client(self, client_socket, addr):
        client_ip = addr[0]
        
        if not self.is_ip_allowed(client_ip):
            self.log_system_async(f"Rejected {client_ip}")
            client_socket.close()
            return
        
        try:
            data = b""
            while True:
                chunk = client_socket.recv(4096)
                if not chunk:
                    break
                data += chunk
                
                try:
                    # TIMESTAMP WHEN MESSAGE ARRIVES
                    receive_timestamp = datetime.now()
                    
                    message = data.decode('utf-8')
                    
                    # Try to parse as JSON first
                    text = None
                    voice_gender = "default"
                    
                    try:
                        message_data = json.loads(message)
                        if isinstance(message_data, dict):
                            text = message_data.get("text", "")
                            voice_gender = message_data.get("voice_gender", "default")
                        else:
                            text = message
                    except (json.JSONDecodeError, TypeError, ValueError):
                        text = message
                    
                    if text:
                        cleaned = self.clean_text(text)
                        
                        if cleaned:
                            if not self.message_queue.put((cleaned, f"TCP:{client_ip}", voice_gender, receive_timestamp)):
                                self.log_system_async(f"Queue full, message dropped from {client_ip}")
        
                    data = b""
                except UnicodeDecodeError:
                    continue
        except Exception as e:
            self.log_system_async(f"TCP error: {e}")
            traceback.print_exc()
        finally:
            client_socket.close()

    def is_port_in_use(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return False
            except socket.error as e:
                return e.errno == errno.EADDRINUSE

    def start_tcp_server(self):
        if self.is_port_in_use(self.tcp_port):
            self.update_status("TCP", f"Port {self.tcp_port} in use")
            return
        
        def run_tcp_server():
            try:
                self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.tcp_socket.bind(('0.0.0.0', self.tcp_port))
                self.tcp_socket.listen(5)
                self.update_status("TCP", "Ready")
                
                while not self.shutdown_event.is_set():
                    readable, _, _ = select.select([self.tcp_socket], [], [], 1.0)
                    
                    if readable:
                        client_socket, addr = self.tcp_socket.accept()
                        
                        client_thread = threading.Thread(
                            target=self.handle_tcp_client,
                            args=(client_socket, addr),
                            daemon=True
                        )
                        client_thread.start()
            
            except Exception as e:
                self.log_system_async(f"TCP error: {e}")
            finally:
                if self.tcp_socket:
                    self.tcp_socket.close()
        
        self.tcp_thread = threading.Thread(target=run_tcp_server, daemon=True)
        self.tcp_thread.start()

    def start_http_server(self):
        if self.is_port_in_use(self.http_port):
            self.update_status("HTTP", f"Port {self.http_port} in use")
            return
        
        def run_http_server():
            try:
                self.httpd = HTTPServer(('', self.http_port), TTSRequestHandler)
                self.httpd.message_queue = self.message_queue
                self.httpd.gui = self
                self.update_status("HTTP", "Ready")
                self.httpd.serve_forever()
            except Exception as e:
                self.log_system_async(f"HTTP error: {e}")
        
        self.http_thread = threading.Thread(target=run_http_server, daemon=True)
        self.http_thread.start()

    def start_servers(self):
        self.start_tcp_server()
        self.start_http_server()

    def on_close(self):
        self.log_system_async("Shutting down...")
        
        # Add shutdown marker to log files if logging enabled
        if self.enable_file_logging.get():
            shutdown_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            shutdown_marker = f"Server stopped: {shutdown_time}\n{'='*80}\n"
            for log_file in [self.system_log_file, self.message_log_file]:
                try:
                    with open(log_file, 'a', encoding='utf-8') as f:
                        f.write(shutdown_marker)
                except:
                    pass
        
        self.shutdown_event.set()
        
        if hasattr(self, 'httpd') and self.httpd:
            self.httpd.shutdown()
            if hasattr(self, 'http_thread'):
                self.http_thread.join(timeout=2)
        
        if hasattr(self, 'tcp_socket') and self.tcp_socket:
            self.tcp_socket.close()
        
        if hasattr(self, 'processor_thread'):
            self.processor_thread.join(timeout=2)
        
        if hasattr(self, 'log_writer_thread'):
            self.log_writer_thread.join(timeout=1)
        
        # Release lock file
        self.release_lock()
        
        self.root.destroy()

    def start(self):
        self.root.mainloop()


def main():
    try:
        print("Starting TTS Server - Low Latency")
        print("Features:")
        print("  - Async TTS (eliminates post-speech silence)")
        print("  - Realtek speaker prioritized (lowest latency)")
        print("  - Voice gender matching (male/female/default)")
        print("  - Accurate receive timestamps")
        print("  - Event-driven logging (minimal CPU, instant display)")
        print("  - Adjustable speech speed (0-3)")
        print("  - Adjustable volume (0-100%)")
        print("  - Single instance enforcement")
        print("  - Optional file logging (disabled by default)")
        print("  - Log window shows last 60 messages")
        server = TTSServerGUI()
        server.start()
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        traceback.print_exc()
        input("Press Enter to exit...")


if __name__ == "__main__":
    main()
