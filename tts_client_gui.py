import socket
import tkinter as tk
from tkinter import ttk, scrolledtext
from threading import Thread, Event
from datetime import datetime
import os

class TTSClientGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("TTS Client")
        self.root.geometry("500x400")
        
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
        
        # Create input area
        self.input_label = ttk.Label(self.main_frame, text="Enter text to speak:")
        self.input_label.grid(row=1, column=0, columnspan=3, sticky=tk.W)
        
        self.input_text = scrolledtext.ScrolledText(self.main_frame, height=5)
        self.input_text.grid(row=2, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # Create send button
        self.send_button = ttk.Button(self.main_frame, text="Send", command=self.send_text)
        self.send_button.grid(row=3, column=0, columnspan=3, sticky=tk.E)
        
        # Create log area
        self.log_label = ttk.Label(self.main_frame, text="Log:")
        self.log_label.grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))
        
        # Add log file path display
        self.log_path_label = ttk.Label(self.main_frame, text=f"Log file: {os.path.abspath(self.log_file)}")
        self.log_path_label.grid(row=5, column=0, columnspan=3, sticky=tk.W)
        
        self.log_text = scrolledtext.ScrolledText(self.main_frame, height=10)
        self.log_text.grid(row=6, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.main_frame.columnconfigure(1, weight=1)
        self.main_frame.rowconfigure(6, weight=1)
        
        # Initialize socket and connection state
        self.socket = None
        self.connected = False
        self.host = '127.0.0.1'
        self.port = 5000
        
        # Event to signal thread termination
        self.shutdown_event = Event()

        # Bind window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)        

    def log_message(self, message):
        now = datetime.now()
        screen_time = now.strftime("%H:%M:%S")
        file_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        
        # Add to GUI log with time only
        screen_entry = f"[{screen_time}] {message}\n"
        self.log_text.insert(tk.END, screen_entry)
        self.log_text.see(tk.END)
        
        # Write to log file with full timestamp
        try:
            file_entry = f"[{file_timestamp}] {message}\n"
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(file_entry)
        except Exception as e:
            error_time = datetime.now().strftime("%H:%M:%S")
            self.log_text.insert(tk.END, f"[{error_time}] Error writing to log file: {e}\n")
            self.log_text.see(tk.END)

    def connect(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            self.connected = True
            self.status_var.set(f"Connected to {self.host}:{self.port}")
            self.connect_button.config(text="Disconnect")
            self.log_message(f"Connected to server at {self.host}:{self.port}")
            self.send_button.config(state=tk.NORMAL)
        except Exception as e:
            self.log_message(f"Connection error: {e}")
            self.status_var.set("Connection failed")
            self.connected = False

    def disconnect(self):
        if self.socket:
            self.socket.close()
            self.socket = None
        self.connected = False
        self.status_var.set("Not Connected")
        self.connect_button.config(text="Connect")
        self.log_message("Disconnected from server")
        self.send_button.config(state=tk.DISABLED)

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
            return
            
        try:
            self.socket.send(text.encode('utf-8'))
            self.log_message(f"Sent message: {text}")
            self.input_text.delete("1.0", tk.END)
        except Exception as e:
            self.log_message(f"Error sending text: {e}")
            self.disconnect()

    def on_close(self):
        """Handle application shutdown."""
        self.shutdown_event.set()  # Signal threads to stop
        self.disconnect()  # Close the socket
        self.root.destroy()  # Close the GUI

    def start(self):
        # Disable send button initially
        self.send_button.config(state=tk.DISABLED)
        
        # Bind Enter key to send
        self.root.bind('<Return>', lambda e: self.send_text())
        
        # Log startup
        self.log_message("Client application started")
        
        # Start the GUI
        self.root.mainloop()

def main():
    client_gui = TTSClientGUI()
    client_gui.start()

if __name__ == "__main__":
    main()