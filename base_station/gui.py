import argparse
import tkinter as tk
import threading
import queue

from tkinter import ttk
from pathlib import Path
from details_panel import DetailsPanel
from packet_receiver import receive_packets
from radio import add_radio_arguments, list_ports

parser = argparse.ArgumentParser(description="Night Owl detection viewer")
add_radio_arguments(parser)
options = parser.parse_args()
if options.list_ports:
    try:
        list_ports()
    except OSError as error:
        parser.exit(1, f"{error}\n")
    parser.exit()
if options.baud <= 0:
    parser.error("--baud must be positive")

root = tk.Tk()
root.title("Night Owl — Base Station")
root.geometry("1350x800")
root.minsize(1150, 750)
root.configure(bg="#101827")

style = ttk.Style(root)
style.theme_use("clam")
style.configure("TFrame", background="#101827")
style.configure("TLabel", background="#101827", foreground="#dbe4f0", font=("Segoe UI", 10))
style.configure("Title.TLabel", foreground="white", font=("Segoe UI", 26, "bold"))
style.configure("Subtitle.TLabel", foreground="#94a3b8", font=("Segoe UI", 11))
style.configure("Status.TLabel", foreground="#67e8f9")
style.configure("TCombobox", fieldbackground="#1e293b", background="#334155", foreground="white", arrowcolor="#67e8f9", padding=7)
style.map("TCombobox", fieldbackground=[("readonly", "#1e293b")], foreground=[("readonly", "white")], selectbackground=[("readonly", "#1e293b")], selectforeground=[("readonly", "white")])
root.option_add("*TCombobox*Listbox.background", "#1e293b")
root.option_add("*TCombobox*Listbox.foreground", "white")
root.option_add("*TCombobox*Listbox.selectBackground", "#2563eb")
root.option_add("*TCombobox*Listbox.selectForeground", "white")
style.configure("Treeview", background="#172235", fieldbackground="#172235", foreground="#e2e8f0", rowheight=36, borderwidth=0, font=("Segoe UI", 10))
style.configure("Treeview.Heading", background="#243248", foreground="#94e7f2", font=("Segoe UI", 10, "bold"), padding=(10, 12), relief="flat")
style.map("Treeview", background=[("selected", "#235782")], foreground=[("selected", "white")])
style.map("Treeview.Heading", background=[("active", "#30425c")])

header = ttk.Frame(root)
header.pack(fill="x", padx=28, pady=(24, 8))

ttk.Label(
    header,
    text="NIGHT OWL",
    style="Title.TLabel",
).pack(anchor="center")

ttk.Label(
    header,
    text="Base Station  •  Detection Viewer",
    style="Subtitle.TLabel",
).pack(anchor="center", pady=(4, 0))

# Centered filter controls
filter_frame = ttk.Frame(root)
filter_frame.pack(pady=(20, 16))

ttk.Label(
    filter_frame,
    text="Object filter",
).pack(side="left", padx=(0, 12))

selected_type = tk.StringVar(value="All")

object_filter = ttk.Combobox(
    filter_frame,
    textvariable=selected_type,
    values=["All", "person", "vehicle"],
    state="readonly",
    width=18,
    font=("Segoe UI", 10),
)
object_filter.pack(side="left")

ttk.Label(
    filter_frame,
    text="Filter received detections by object type",
    style="Subtitle.TLabel",
).pack(side="left", padx=18)

status_text = tk.StringVar(value="Starting receiver...")

ttk.Label(
    root,
    textvariable=status_text,
    style="Status.TLabel",
    anchor="center",
).pack(
    side="bottom",
    fill="x",
    padx=28,
    pady=(10, 18),
)

# Table and independent selected-detection panel
content = ttk.Frame(root)
content.pack(fill="both", expand=True, padx=28, pady=(0, 12))
panes = tk.PanedWindow(content, orient="horizontal", bg="#101827", bd=0,
                       sashwidth=12, sashrelief="flat", sashcursor="sb_h_double_arrow",
                       opaqueresize=True)
panes.pack(fill="both", expand=True)
table_frame = ttk.Frame(panes)
table_frame.rowconfigure(0, weight=1)
table_frame.columnconfigure(0, weight=1)
details = DetailsPanel(panes, Path(__file__).resolve().parent)
panes.add(table_frame, minsize=590, stretch="always")
panes.add(details, minsize=380, width=520, stretch="never")

columns = ("type", "confidence", "time", "latitude", "longitude")

table = ttk.Treeview(
    table_frame,
    columns=columns,
    show="headings",
    selectmode="browse",
)

headings = {
    "type": "OBJECT",
    "confidence": "CONFIDENCE",
    "time": "DETECTION TIME",
    "latitude": "LATITUDE",
    "longitude": "LONGITUDE",
}

for column in columns:
    table.heading(column, text=headings[column], anchor="center")
    table.column(column, width=150, minwidth=100, anchor="center")

table.column("time", width=230, minwidth=190)

scrollbar = ttk.Scrollbar(
    table_frame,
    orient="vertical",
    command=table.yview,
)
table.configure(yscrollcommand=scrollbar.set)

horizontal_scrollbar = ttk.Scrollbar(table_frame, orient="horizontal", command=table.xview)
table.configure(xscrollcommand=horizontal_scrollbar.set)
table.grid(row=0, column=0, sticky="nsew")
scrollbar.grid(row=0, column=1, sticky="ns")
horizontal_scrollbar.grid(row=1, column=0, sticky="ew")

table.tag_configure("even", background="#172235")
table.tag_configure("odd", background="#1c2a40")

detections = []

def show_selected(event=None):
    selected = table.selection()
    if selected:
        details.show_detection(detections[int(selected[0])])


table.bind("<<TreeviewSelect>>", show_selected)


def update_table(event=None):
    selected = table.selection()
    previous = selected[0] if selected else None
    for row in table.get_children():
        table.delete(row)

    for index, detection in enumerate(detections):
        if (
            selected_type.get() == "All"
            or detection.object_type == selected_type.get()
        ):
            table.insert(
                "",
                "end",
                iid=str(index),
                tags=(
                    "even"
                    if len(table.get_children()) % 2 == 0
                    else "odd",
                ),
                values=(
                    detection.object_type,
                    f"{detection.confidence:.0%}",
                    detection.formatted_time,
                    detection.formatted_latitude,
                    detection.formatted_longitude,
                ),
            )

    if previous is not None and table.exists(previous):
        table.selection_set(previous)
    else:
        details.clear()


object_filter.bind("<<ComboboxSelected>>", update_table)
incoming = queue.Queue()


def check_incoming():
    for _ in range(100):
        try:
            message_type, data = incoming.get_nowait()
        except queue.Empty:
            break

        if message_type == "detection":
            detections.append(data)

            choices = list(object_filter["values"])
            if data.object_type not in choices:
                choices.append(data.object_type)
                object_filter["values"] = choices

            update_table()

            status_text.set(
                f"Received {len(detections)} detections — "
                f"latest: {data.object_type}"
            )
        elif message_type == "image":
            for detection in detections:
                if detection.packet_id == data["packet_id"]:
                    detection.image_path = data["image_path"]
                    if details.current_detection is detection:
                        details.show_detection(detection)
            status_text.set(f"Received thumbnail for detection {data['packet_id']}")
        else:
            status_text.set(data)

    root.after(100, check_incoming)


threading.Thread(
    target=receive_packets,
    args=(incoming,),
    kwargs={"serial_port": options.serial_port, "baud": options.baud},
    daemon=True,
).start()

check_incoming()
root.mainloop()
