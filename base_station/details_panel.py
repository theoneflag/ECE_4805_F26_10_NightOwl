"""Selected detection details, kept separate from networking and the table."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
import zipfile

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None


def selected_data(detection, choices):
    """Build exported data from the user's checked fields."""
    data = {}
    if "Packet ID" in choices:
        data["packet_id"] = detection.packet_id
    object_data = {}
    if "Object type" in choices:
        object_data["type"] = detection.object_type
    if "Confidence" in choices:
        object_data["confidence"] = detection.confidence
    if object_data:
        data["object"] = object_data
    if "Timestamp" in choices:
        data["timestamp"] = detection.timestamp
    position = {}
    if "GPS location" in choices:
        position.update(latitude=detection.latitude, longitude=detection.longitude)
    if "Altitude" in choices:
        position["altitude_m"] = detection.altitude_m
    if position:
        data["position"] = position
    return data


class DetailsPanel(ttk.Frame):
    def __init__(self, parent, image_directory):
        super().__init__(parent, width=360)
        self.pack_propagate(False)
        self.canvas = tk.Canvas(
            self, background="#101827", highlightthickness=0, width=340,
        )
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(self.canvas, padding=(0, 0, 12, 16))
        self.body = body
        self.window_id = self.canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_body)
        self.image_directory = Path(image_directory)
        self.photo = None
        self.current_detection = None
        ttk.Label(body, text="SELECTED DETECTION",
                  font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 12))
        style = ttk.Style(self)
        style.configure("Details.TCheckbutton", background="#101827",
                        foreground="#dbe4f0", font=("Segoe UI", 10))
        style.map("Details.TCheckbutton", background=[("active", "#1e293b")],
                  foreground=[("active", "white")])
        ttk.Label(body, text="Show information", style="Subtitle.TLabel").pack(anchor="w")
        controls = ttk.Frame(body)
        controls.pack(fill="x", pady=(6, 16))
        self.visible = {}
        options = ("GPS location", "Timestamp", "Image", "Packet ID",
                   "Confidence", "Object type", "Altitude")
        for index, name in enumerate(options):
            enabled = tk.BooleanVar(master=self, value=True)
            self.visible[name] = enabled
            ttk.Checkbutton(
                controls, text=name, variable=enabled,
                command=self._apply_visibility, style="Details.TCheckbutton",
            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 12), pady=4)
        self.export_button = ttk.Button(
            body, text="Export selected detection",
            command=self.export_selected, state="disabled",
        )
        self.export_button.pack(fill="x", pady=(0, 16))
        self.row_widgets = {}
        self.field_options = {
            "Packet ID": "Packet ID", "Object": "Object type",
            "Confidence": "Confidence", "Time (local)": "Timestamp",
            "Latitude": "GPS location", "Longitude": "GPS location",
            "Altitude (m)": "Altitude",
        }
        self.fields = {}
        metadata = ttk.Frame(body)
        metadata.pack(fill="x", pady=(0, 12))
        metadata.columnconfigure(1, weight=1)

        names = (
            "Packet ID", "Object", "Confidence", "Time (local)",
            "Latitude", "Longitude", "Altitude (m)",
        )
        for row, name in enumerate(names):
            label = ttk.Label(metadata, text=name, style="Subtitle.TLabel")
            label.grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
            value = tk.StringVar(master=self, value="—")
            self.fields[name] = value
            display = ttk.Label(metadata, textvariable=value, wraplength=170, justify="left")
            display.grid(row=row, column=1, sticky="w", pady=6)
            self.row_widgets[name] = (label, display)

        self.image_frame = ttk.Frame(body)
        self.image_frame.pack(fill="x")
        ttk.Label(
            self.image_frame, text="Detection image", style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 8))
        self.image_label = ttk.Label(
            self.image_frame, text="Select a detection to view its details.",
            anchor="center", wraplength=290,
        )
        self.image_label.pack(fill="x")
        self.file_label = ttk.Label(
            self.image_frame, text="", wraplength=240, anchor="w",
            justify="left", style="Subtitle.TLabel",
        )
        self.file_label.pack(fill="x", padx=(0, 12), pady=8)

        self._bind_wheel(body)
        self._bind_wheel(self.canvas)

    def _apply_visibility(self):
        # Hiding a widget keeps the detection data and image reference intact.
        for name, widgets in self.row_widgets.items():
            for widget in widgets:
                if self.visible[self.field_options[name]].get():
                    widget.grid()
                else:
                    widget.grid_remove()
        if self.visible["Image"].get():
            self.image_frame.pack(fill="x")
        else:
            self.image_frame.pack_forget()
        self.after_idle(self._update_scroll_region)

    def _update_scroll_region(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_body(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)
        self._update_scroll_region()

    def _bind_wheel(self, widget):
        widget.bind("<MouseWheel>", self._scroll_wheel)
        widget.bind("<Button-4>", lambda event: self._scroll_units(-1))
        widget.bind("<Button-5>", lambda event: self._scroll_units(1))
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _scroll_units(self, units):
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, "units")
        return "break"

    def _scroll_wheel(self, event):
        if event.delta:
            return self._scroll_units(-1 if event.delta > 0 else 1)
        return "break"

    def export_selected(self):
        detection = self.current_detection
        if detection is None:
            return
        choices = {name for name, value in self.visible.items() if value.get()}
        if not choices:
            messagebox.showinfo("Choose information", "Check at least one information field.", parent=self)
            return
        data = selected_data(detection, choices)
        image_path = None
        if "Image" in choices:
            reference = detection.image_path
            if not isinstance(reference, str) or not reference:
                messagebox.showerror("Image unavailable",
                                     "This detection has no image. Uncheck Image to export its other fields.",
                                     parent=self)
                return
            image_path = Path(reference)
            if not image_path.is_absolute():
                image_path = self.image_directory / image_path
            if not image_path.is_file():
                messagebox.showerror("Image unavailable",
                                     "The image file is missing on this laptop. Uncheck Image to export other fields.",
                                     parent=self)
                return
            data["thumbnail"] = "image" + image_path.suffix.lower()
        destination = filedialog.asksaveasfilename(
            parent=self, title="Export checked detection information",
            initialfile="detection_export.zip", defaultextension=".zip",
            filetypes=[("ZIP archive", "*.zip")],
        )
        if not destination:
            return
        try:
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("detection.json", json.dumps(data, indent=2, allow_nan=False))
                if image_path is not None:
                    archive.write(image_path, data["thumbnail"])
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            messagebox.showerror("Export failed", str(error), parent=self)
            return
        messagebox.showinfo("Export saved",
                            "Saved checked information in detection.json"
                            + (" and included the image." if image_path else "."),
                            parent=self)

    def clear(self):
        self.current_detection = None
        self.export_button.configure(state="disabled")
        for value in self.fields.values():
            value.set("—")
        self.photo = None
        self.image_label.configure(image="", text="Select a detection to view its details.")
        self.file_label.configure(text="")

    def show_detection(self, detection):
        self.current_detection = detection
        self.export_button.configure(state="normal")
        values = {
            "Packet ID": str(detection.packet_id),
            "Object": detection.object_type,
            "Confidence": f"{detection.confidence:.0%}",
            "Time (local)": detection.formatted_time,
            "Latitude": f"{detection.latitude:.6f}",
            "Longitude": f"{detection.longitude:.6f}",
            "Altitude (m)": "Not provided" if detection.altitude_m is None else str(detection.altitude_m),
        }
        for name, value in values.items():
            self.fields[name].set(value)
        self.photo = None
        self.image_label.configure(image="", text="")
        reference = detection.image_path
        self.file_label.configure(text=str(reference or "No image reference in packet"))
        if not reference:
            self.image_label.configure(text="No image provided")
            return
        if not isinstance(reference, str):
            self.image_label.configure(text="Invalid image reference")
            return
            
        # Resolve local thumbnail references relative to the application folder.
        path = Path(reference)
        if not path.is_absolute():
            path = self.image_directory / path
        if not path.is_file():
            self.image_label.configure(text="Image file not available on this laptop")
            return
        if Image is None:
            self.image_label.configure(text="Install Pillow to display JPG/PNG images")
            return
        try:
            with Image.open(path) as source:
                preview = source.convert("RGB")
                preview.thumbnail((290, 180))
            self.photo = ImageTk.PhotoImage(preview, master=self)
            self.image_label.configure(image=self.photo, text="")
        except (OSError, ValueError, Image.DecompressionBombError):
            self.image_label.configure(text="Unable to open this image")
