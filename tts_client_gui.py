import socket
import tkinter as tk
from tkinter import ttk, scrolledtext
from threading import Thread, Event
from datetime import datetime
import os
import time
import json

class TTSClientGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("TTS Client")
        self.root.geometry("500x550")
        
        # Setup logging directory
        self.log_directory = "client_logs"
        if not os.path.exists(self.log_directory):
            os.makedirs(self.log_directory)
        self.log_file = os.path.join(
            self.log_directory, 
            f"client_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        
        # Create the main frame
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Connection status
        self.status_var = tk.StringVar(value="Not Connected")
        self.status_label = ttk.Label(self.main_frame, textvariable=self.status_var)
        self.status_label.grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))
        
        # Create connect/disconnect button
        self.connect_button = ttk.Button(self.main_frame, text="Connect", command=self.toggle_connection)
        self.connect_button.grid(row=0, column=2, sticky=tk.E, pady=(0, 10))
        
        # Voice gender selection
        voice_frame = ttk.LabelFrame(self.main_frame, text="Voice Preference", padding="5")
        voice_frame.grid(row=1, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        
        ttk.Label(voice_frame, text="Voice:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        
        self.voice_gender = tk.StringVar(value="default")
        
        ttk.Radiobutton(voice_frame, text="Server Default", variable=self.voice_gender, value="default").grid(row=0, column=1, padx=5)
        ttk.Radiobutton(voice_frame, text="Male", variable=self.voice_gender, value="male").grid(row=0, column=2, padx=5)
        ttk.Radiobutton(voice_frame, text="Female", variable=self.voice_gender, value="female").grid(row=0, column=3, padx=5)
        
        voice_frame.columnconfigure(1, weight=1)
        
        # Create input area
        self.input_label = ttk.Label(self.main_frame, text="Enter text to speak:")
        self.input_label.grid(row=2, column=0, columnspan=3, sticky=tk.W)
        
        self.input_text = scrolledtext.ScrolledText(self.main_frame, height=5)
        self.input_text.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # Button frame for Send and Clear
        button_frame = ttk.Frame(self.main_frame)
        button_frame.grid(row=4, column=0, columnspan=3, sticky=(tk.W, tk.E))
        
        self.send_button = ttk.Button(button_frame, text="Send", command=self.send_text)
        self.send_button.grid(row=0, column=0, padx=(0, 5))
        
        self.clear_button = ttk.Button(button_frame, text="Clear", command=self.clear_text)
        self.clear_button.grid(row=0, column=1)
        
        # Auto-send controls
        auto_frame = ttk.LabelFrame(self.main_frame, text="Auto Send", padding="5")
        auto_frame.grid(row=5, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(10, 10))
        
        ttk.Label(auto_frame, text="Interval (seconds):").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        
        self.auto_interval = tk.IntVar(value=5)
        self.interval_spinbox = ttk.Spinbox(
            auto_frame,
            from_=1,
            to=3600,
            textvariable=self.auto_interval,
            width=10
        )
        self.interval_spinbox.grid(row=0, column=1, padx=(0, 10))
        
        self.auto_send_active = False
        self.auto_send_button = ttk.Button(auto_frame, text="Start Auto", command=self.toggle_auto_send)
        self.auto_send_button.grid(row=0, column=2)
        
        self.auto_status_label = ttk.Label(auto_frame, text="Status: Stopped", foreground="gray")
        self.auto_status_label.grid(row=0, column=3, padx=(10, 0))
        
        auto_frame.columnconfigure(1, weight=1)
        
        # Create log area
        self.log_label = ttk.Label(self.main_frame, text="Log:")
        self.log_label.grid(row=6, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))
        
        # Add log file path display
        self.log_path_label = ttk.Label(self.main_frame, text=f"Log file: {os.path.abspath(self.log_file)}")
        self.log_path_label.grid(row=7, column=0, columnspan=3, sticky=tk.W)
        
        self.log_text = scrolledtext.ScrolledText(self.main_frame, height=6)
        self.log_text.grid(row=8, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.main_frame.columnconfigure(1, weight=1)
        self.main_frame.rowconfigure(8, weight=1)
        
        # Initialize socket and connection state
        self.socket = None
        self.connected = False
        self.host = '127.0.0.1'
        self.port = 5000
        
        # Event to signal thread termination
        self.shutdown_event = Event()
        
        # Auto-send thread
        self.auto_send_thread = None
        self.auto_send_event = Event()

        # Bind window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)        

    def log_message(self, message):
        now = datetime.now()
        screen_time = now.strftime("%H:%M:%S.%f")[:-3]  # Add milliseconds
        file_timestamp = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # Add milliseconds
        
        # Add to GUI log with time and milliseconds
        screen_entry = f"[{screen_time}] {message}\n"
        self.log_text.insert(tk.END, screen_entry)
        self.log_text.see(tk.END)
        
        # Write to log file with full timestamp and milliseconds
        try:
            file_entry = f"[{file_timestamp}] {message}\n"
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(file_entry)
        except Exception as e:
            error_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self.log_text.insert(tk.END, f"[{error_time}] Error writing to log file: {e}\n")
            self.log_text.see(tk.END)

    def clear_text(self):
        """Clear the input text field"""
        self.input_text.delete("1.0", tk.END)
        self.log_message("Input field cleared")

    def connect(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            self.connected = True
            self.status_var.set(f"Connected to {self.host}:{self.port}")
            self.connect_button.config(text="Disconnect")
            self.log_message(f"Connected to server at {self.host}:{self.port}")
            self.send_button.config(state=tk.NORMAL)
            self.clear_button.config(state=tk.NORMAL)
        except Exception as e:
            self.log_message(f"Connection error: {e}")
            self.status_var.set("Connection failed")
            self.connected = False

    def disconnect(self):
        # Stop auto-send if running
        if self.auto_send_active:
            self.stop_auto_send()
        
        if self.socket:
            self.socket.close()
            self.socket = None
        self.connected = False
        self.status_var.set("Not Connected")
        self.connect_button.config(text="Connect")
        self.log_message("Disconnected from server")
        self.send_button.config(state=tk.DISABLED)
        self.clear_button.config(state=tk.DISABLED)

    def toggle_connection(self):
        if not self.connected:
            Thread(target=self.connect).start()
        else:
            self.disconnect()

    def send_text(self):
        if not self.connected:
            self.log_message("Not connected to server")
            return
            
        text = self.input_text.get("1.0", tk.END).strip()
        if not text:
            self.log_message("No text to send")
            return
        
        # Get voice preference
        voice_pref = self.voice_gender.get()
        
        # Create message with voice preference
        message_data = {
            "text": text,
            "voice_gender": voice_pref
        }
        
        try:
            # Send as JSON
            json_message = json.dumps(message_data)
            self.socket.send(json_message.encode('utf-8'))
            
            if voice_pref == "default":
                self.log_message(f"Sent: {text} (server default voice)")
            else:
                self.log_message(f"Sent: {text} ({voice_pref} voice)")
        except Exception as e:
            self.log_message(f"Error sending text: {e}")
            self.disconnect()

    def toggle_auto_send(self):
        """Toggle auto-send on/off"""
        if not self.auto_send_active:
            self.start_auto_send()
        else:
            self.stop_auto_send()

    def start_auto_send(self):
        """Start automatic periodic sending"""
        if not self.connected:
            self.log_message("Cannot start auto-send: Not connected to server")
            return
        
        text = self.input_text.get("1.0", tk.END).strip()
        if not text:
            self.log_message("Cannot start auto-send: No text in input field")
            return
        
        self.auto_send_active = True
        self.auto_send_event.clear()
        
        # Update UI
        self.auto_send_button.config(text="Stop Auto")
        self.auto_status_label.config(text="Status: Running", foreground="green")
        self.interval_spinbox.config(state="disabled")
        self.send_button.config(state=tk.DISABLED)
        
        interval = self.auto_interval.get()
        self.log_message(f"Auto-send started (every {interval} seconds)")
        
        # Start auto-send thread
        self.auto_send_thread = Thread(target=self.auto_send_worker, daemon=True)
        self.auto_send_thread.start()

    def stop_auto_send(self):
        """Stop automatic periodic sending"""
        if not self.auto_send_active:
            return
        
        self.auto_send_active = False
        self.auto_send_event.set()
        
        # Update UI
        self.auto_send_button.config(text="Start Auto")
        self.auto_status_label.config(text="Status: Stopped", foreground="gray")
        self.interval_spinbox.config(state="normal")
        if self.connected:
            self.send_button.config(state=tk.NORMAL)
        
        self.log_message("Auto-send stopped")

    def auto_send_worker(self):
        """Worker thread for auto-sending"""
        interval = self.auto_interval.get()
        
        while self.auto_send_active and not self.shutdown_event.is_set():
            # Check if still connected
            if not self.connected:
                self.log_message("Auto-send stopped: Connection lost")
                self.root.after(0, self.stop_auto_send)
                break
            
            # Get text from input field
            text = self.input_text.get("1.0", tk.END).strip()
            
            if not text:
                self.log_message("Auto-send stopped: Input field is empty")
                self.root.after(0, self.stop_auto_send)
                break
            
            # Get voice preference
            voice_pref = self.voice_gender.get()
            
            # Create message with voice preference
            message_data = {
                "text": text,
                "voice_gender": voice_pref
            }
            
            # Send the message
            try:
                json_message = json.dumps(message_data)
                self.socket.send(json_message.encode('utf-8'))
                
                if voice_pref == "default":
                    self.log_message(f"Auto-sent: {text} (server default)")
                else:
                    self.log_message(f"Auto-sent: {text} ({voice_pref})")
            except Exception as e:
                self.log_message(f"Auto-send error: {e}")
                self.root.after(0, self.disconnect)
                break
            
            # Wait for the interval (or until stop signal)
            if self.auto_send_event.wait(timeout=interval):
                break  # Stop signal received

    def on_close(self):
        """Handle application shutdown."""
        self.shutdown_event.set()
        
        # Stop auto-send
        if self.auto_send_active:
            self.stop_auto_send()
        
        # Disconnect
        self.disconnect()
        
        # Close GUI
        self.root.destroy()

    def start(self):
        # Disable send and clear buttons initially
        self.send_button.config(state=tk.DISABLED)
        self.clear_button.config(state=tk.DISABLED)
        
        # Bind Ctrl+Enter to insert newline
        self.root.bind('<Control-Return>', lambda e: self.input_text.insert(tk.INSERT, '\n'))
        
        # Log startup
        self.log_message("Client application started")
        
        # Start the GUI
        self.root.mainloop()

def main():
    client_gui = TTSClientGUI()
    client_gui.start()

if __name__ == "__main__":
    main()
