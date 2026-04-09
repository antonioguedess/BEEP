import csv
import os
import sys
from math import ceil

import cv2
import numpy as np
import tkinter as tk
from PIL import Image, ImageTk
from tkinter import filedialog, messagebox

# ================= CONFIG =================
MAX_ZOOM = 10.0
MIN_ZOOM = 0.1
ZOOM_STEP = 0.1
CANVAS_SIZE = (900, 1600)
DEFAULT_RATIO_L = 1
DEFAULT_RATIO_H = 1

# ================= GLOBAL STATE =================
points_original = []
zoom = 1.0
pan_x = 0
pan_y = 0
mouse_last = None
selecting_pan = False
threshold_value = None


# ================= FUNCTIONS =================
def screen_to_image_coords(x, y):
    ix = int((x - pan_x) / zoom)
    iy = int((y - pan_y) / zoom)
    return ix, iy


def zoom_at_point(cursor_x, cursor_y, new_zoom):
    global zoom, pan_x, pan_y
    image_x = (cursor_x - pan_x) / zoom
    image_y = (cursor_y - pan_y) / zoom
    zoom = new_zoom
    pan_x = int(round(cursor_x - image_x * zoom))
    pan_y = int(round(cursor_y - image_y * zoom))


def order_points(pts):
    pts = np.array(pts, dtype="float32")
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1)
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(diff)]
    bl = pts[np.argmax(diff)]
    return np.array([tl, tr, br, bl], dtype="float32")


def four_point_transform(image, pts, out_w, out_h):
    rect = order_points(pts)
    dst = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], dtype="float32")
    matrix = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, matrix, (out_w, out_h))
    return warped


def ask_csv_interval(default_interval):
    raw_value = input(
        f"How many frames should pass between CSV records? (Enter = {default_interval}): "
    ).strip()
    if raw_value == "":
        return default_interval
    try:
        interval = int(raw_value)
        if interval < 1:
            raise ValueError
        return interval
    except ValueError:
        print(f"Invalid value. The default value ({default_interval}) will be used.")
        return default_interval


def estimate_threshold_from_first_seconds(
    cap,
    pts,
    out_w,
    out_h,
    fps,
    analysis_seconds=3,
    min_black_percentage=0.1,
):
    frames_to_analyze = max(1, int(round(fps * analysis_seconds)))
    gray_frames = []

    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    for _ in range(frames_to_analyze):
        ret, frame = cap.read()
        if not ret:
            break

        warped = four_point_transform(frame, pts, out_w, out_h)
        gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
        gray_frames.append(gray_warped)

    selected_threshold = 255

    for threshold in range(256):
        found_black = False
        for gray_warped in gray_frames:
            _, bw = cv2.threshold(gray_warped, threshold, 255, cv2.THRESH_BINARY)
            black_percentage = (np.count_nonzero(bw == 0) / bw.size) * 100
            if black_percentage >= min_black_percentage:
                selected_threshold = threshold
                found_black = True
                break
        if found_black:
            break

    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    return selected_threshold


# ================= MOUSE EVENTS =================
def mouse_event(event, x, y, flags, param):
    global zoom, pan_x, pan_y, mouse_last, selecting_pan, points_original
    if event == cv2.EVENT_MOUSEWHEEL:
        old_zoom = zoom
        if flags > 0:
            new_zoom = min(MAX_ZOOM, zoom + ZOOM_STEP)
        else:
            new_zoom = max(MIN_ZOOM, zoom - ZOOM_STEP)
        if new_zoom != old_zoom:
            zoom_at_point(x, y, new_zoom)
    elif event in (cv2.EVENT_RBUTTONDOWN, cv2.EVENT_MBUTTONDOWN):
        selecting_pan = True
        mouse_last = (x, y)
    elif event in (cv2.EVENT_RBUTTONUP, cv2.EVENT_MBUTTONUP):
        selecting_pan = False
        mouse_last = None
    elif event == cv2.EVENT_MOUSEMOVE and selecting_pan:
        dx = x - mouse_last[0]
        dy = y - mouse_last[1]
        pan_x += dx
        pan_y += dy
        mouse_last = (x, y)
    elif event == cv2.EVENT_LBUTTONDOWN:
        ix = int((x - pan_x) / zoom)
        iy = int((y - pan_y) / zoom)
        if 0 <= ix < param.shape[1] and 0 <= iy < param.shape[0]:
            if len(points_original) < 4:
                points_original.append((ix, iy))
                print(f"Point {len(points_original)}: {ix, iy}")


# ================= DRAWING =================
def draw_window(img):
    zoomed = cv2.resize(img, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_LINEAR)
    canvas = np.zeros((CANVAS_SIZE[0], CANVAS_SIZE[1], 3), dtype=np.uint8)
    ch, cw = canvas.shape[:2]

    src_x0 = max(0, -pan_x)
    src_y0 = max(0, -pan_y)
    dst_x0 = max(0, pan_x)
    dst_y0 = max(0, pan_y)

    visible_w = min(zoomed.shape[1] - src_x0, cw - dst_x0)
    visible_h = min(zoomed.shape[0] - src_y0, ch - dst_y0)

    if visible_w > 0 and visible_h > 0:
        canvas[dst_y0:dst_y0 + visible_h, dst_x0:dst_x0 + visible_w] = (
            zoomed[src_y0:src_y0 + visible_h, src_x0:src_x0 + visible_w]
        )

    for point in points_original:
        sx = int(point[0] * zoom + pan_x)
        sy = int(point[1] * zoom + pan_y)
        if 0 <= sx < cw and 0 <= sy < ch:
            cv2.circle(canvas, (sx, sy), 6, (0, 255, 0), -1)

    cv2.imshow("Zoom & Pan Selector", canvas)


def ask_user_options():
    global create_csv, show_first_frame
    root = tk.Tk()
    root.withdraw()
    create_csv = messagebox.askyesno(
        "CSV Option",
        "Do you want to create a CSV with the black percentage per frame?",
    )
    show_first_frame = messagebox.askyesno(
        "Preview First Frame",
        "Do you want to preview the first binarized frame?",
    )
    root.destroy()


# ================= TKINTER VIEWER WITH SAVE BUTTON =================
class ResultViewer:
    def __init__(self, img, title="Result"):
        self.img = img
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.drag_start = None
        self.root = tk.Tk()
        self.root.title(title)
        self.canvas = tk.Canvas(self.root, width=800, height=800, bg="black")
        self.canvas.pack(fill="both", expand=True)
        self.btn_save = tk.Button(self.root, text="Save Image", command=self.save_image)
        self.btn_save.pack()
        self.photo = None
        self.canvas.bind("<MouseWheel>", self.zoom)
        self.canvas.bind("<ButtonPress-1>", self.start_pan)
        self.canvas.bind("<B1-Motion>", self.do_pan)
        self.canvas.bind("<ButtonPress-2>", self.start_pan)
        self.canvas.bind("<B2-Motion>", self.do_pan)
        self.canvas.bind("<ButtonRelease-1>", self.stop_pan)
        self.canvas.bind("<ButtonRelease-2>", self.stop_pan)
        self.canvas.bind("<ButtonPress-3>", self.start_pan)
        self.canvas.bind("<B3-Motion>", self.do_pan)
        self.canvas.bind("<ButtonRelease-3>", self.stop_pan)
        self.canvas.bind("<Configure>", self.redraw)
        self.redraw()
        self.root.mainloop()

    def zoom(self, event):
        factor = 1.1 if event.delta > 0 else 0.9
        new_scale = max(0.1, min(10.0, self.scale * factor))
        image_x = (event.x - self.offset_x) / self.scale
        image_y = (event.y - self.offset_y) / self.scale
        self.scale = new_scale
        self.offset_x = int(round(event.x - image_x * self.scale))
        self.offset_y = int(round(event.y - image_y * self.scale))
        self.redraw()

    def start_pan(self, event):
        self.drag_start = (event.x, event.y)

    def do_pan(self, event):
        if self.drag_start is None:
            return
        dx = event.x - self.drag_start[0]
        dy = event.y - self.drag_start[1]
        self.offset_x += dx
        self.offset_y += dy
        self.drag_start = (event.x, event.y)
        self.redraw()

    def stop_pan(self, event):
        self.drag_start = None

    def redraw(self, event=None):
        img = self.img
        if len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        else:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        h, w = img.shape[:2]
        resized = cv2.resize(img, (int(w * self.scale), int(h * self.scale)), interpolation=cv2.INTER_NEAREST)
        self.photo = ImageTk.PhotoImage(Image.fromarray(resized))
        self.canvas.delete("all")
        self.canvas.create_image(self.offset_x, self.offset_y, anchor="nw", image=self.photo)

    def save_image(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")],
        )
        if path:
            cv2.imwrite(path, self.img)
            messagebox.showinfo("Saved", f"Image saved to {path}")


# ================= MAIN =================
if __name__ == "__main__":

    if len(sys.argv) > 1:
        video_path = sys.argv[1]
    else:
        root = tk.Tk()
        root.withdraw()
        video_path = filedialog.askopenfilename(
            title="Select the video",
            filetypes=[("Videos", "*.mp4 *.avi *.mov *.mkv")],
        )
        root.destroy()

    if not video_path:
        print("No video selected.")
    elif not os.path.exists(video_path):
        print("File not found.")
    else:
        def main_video(path):
            global points_original
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                print("Error opening video.")
                return
            ret, frame = cap.read()
            if not ret:
                print("Could not read the first frame.")
                return

            cv2.namedWindow("Zoom & Pan Selector", cv2.WINDOW_NORMAL)
            cv2.setMouseCallback("Zoom & Pan Selector", mouse_event, frame)

            print("=== CONTROLS ===")
            print("Scroll -> Zoom")
            print("Right mouse button + drag -> Pan")
            print("Left mouse button -> Select 4 points")
            print("ENTER -> Confirm points and continue")
            print("R -> Reset points")

            while True:
                draw_window(frame)
                key = cv2.waitKey(10)
                if key == ord("r"):
                    points_original = []
                    print("Points reset.")
                if key in [13, 32] and len(points_original) == 4:
                    break
                if key == 27:
                    print("Cancelled.")
                    cap.release()
                    return

            cv2.destroyAllWindows()
            default_ratio = f"{DEFAULT_RATIO_L}:{DEFAULT_RATIO_H}"
            ratio = input(
                f"L:H ratio (e.g. 1:1,16:9) (Enter = {default_ratio}): "
            ) or default_ratio
            try:
                w_ratio, h_ratio = map(float, ratio.split(":"))
            except Exception:
                w_ratio, h_ratio = DEFAULT_RATIO_L, DEFAULT_RATIO_H

            base = 1000
            out_w = int(ceil(base * (w_ratio / min(w_ratio, h_ratio))))
            out_h = int(ceil(base * (h_ratio / min(w_ratio, h_ratio))))

            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0:
                fps = 1
            csv_default_interval = max(1, int(round(fps)))

            thr_input = input(
                "Threshold (0-255) or Enter for automatic analysis of the first 3 seconds: "
            )
            csv_interval = ask_csv_interval(csv_default_interval)

            auto_threshold = None
            if thr_input.strip() == "":
                auto_threshold = estimate_threshold_from_first_seconds(
                    cap,
                    points_original,
                    out_w,
                    out_h,
                    fps,
                )
                print(
                    "Automatic threshold computed from the first 3 seconds "
                    f"to reach at least 0.1% black: {auto_threshold}"
                )

            bw_frames = []
            black_percentages = []
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

            frame_idx = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                warped = four_point_transform(frame, points_original, out_w, out_h)
                gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
                if auto_threshold is not None:
                    _, bw = cv2.threshold(gray_warped, auto_threshold, 255, cv2.THRESH_BINARY)
                else:
                    thresh_val = max(0, min(255, int(thr_input)))
                    _, bw = cv2.threshold(gray_warped, thresh_val, 255, cv2.THRESH_BINARY)
                bw_frames.append(bw)

                black = np.count_nonzero(bw == 0)
                total = bw.size
                black_percentage = (black / total) * 100
                if frame_idx % csv_interval == 0:
                    time_sec = frame_idx / fps
                    black_percentages.append((frame_idx, time_sec, black_percentage))
                frame_idx += 1

            cap.release()

            csv_path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv")],
            )
            if csv_path:
                with open(csv_path, "w", newline="") as csv_file:
                    writer = csv.writer(csv_file)
                    writer.writerow(["Frame", "Time (s)", "Black Percentage"])
                    writer.writerows(black_percentages)
                print(f"CSV saved to {csv_path}")

            if bw_frames:
                ResultViewer(bw_frames[0], "First Binarized Frame")

        main_video(video_path)