import cv2
import numpy as np
import sys
import os
from math import ceil
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk

# ================= CONFIG =================
MAX_ZOOM = 10.0
MIN_ZOOM = 0.1
ZOOM_STEP = 0.1
CANVAS_SIZE = (900, 1600)

# ================= ESTADO GLOBAL =================
points_original = []
zoom = 1.0
pan_x = 0
pan_y = 0
mouse_last = None
selecting_pan = False
threshold_value = None

# ================= FUNÇÕES =================
def screen_to_image_coords(x, y):
    ix = int((x - pan_x) / zoom)
    iy = int((y - pan_y) / zoom)
    return ix, iy

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
    dst = np.array([[0,0],[out_w-1,0],[out_w-1,out_h-1],[0,out_h-1]], dtype="float32")
    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, M, (out_w, out_h))
    return warped

# ================= EVENTOS DO RATO =================
def mouse_event(event, x, y, flags, param):
    global zoom, pan_x, pan_y, mouse_last, selecting_pan, points_original
    if event == cv2.EVENT_MOUSEWHEEL:
        if flags > 0:
            zoom = min(MAX_ZOOM, zoom + ZOOM_STEP)
        else:
            zoom = max(MIN_ZOOM, zoom - ZOOM_STEP)
    elif event == cv2.EVENT_RBUTTONDOWN:
        selecting_pan = True
        mouse_last = (x, y)
    elif event == cv2.EVENT_RBUTTONUP:
        selecting_pan = False
    elif event == cv2.EVENT_MOUSEMOVE and selecting_pan:
        dx = x - mouse_last[0]
        dy = y - mouse_last[1]
        pan_x += dx
        pan_y += dy
        mouse_last = (x, y)
    elif event == cv2.EVENT_LBUTTONDOWN:
        ix, iy = screen_to_image_coords(x, y)
        if 0 <= ix < param.shape[1] and 0 <= iy < param.shape[0]:
            if len(points_original) < 4:
                points_original.append((ix, iy))
                print(f"Ponto {len(points_original)}: {ix, iy}")

# ================= DESENHO =================
def draw_window(img):
    h, w = img.shape[:2]
    zoomed = cv2.resize(img, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_LINEAR)
    canvas = np.zeros((CANVAS_SIZE[0], CANVAS_SIZE[1], 3), dtype=np.uint8)
    ch, cw = canvas.shape[:2]
    x0 = max(0, pan_x)
    y0 = max(0, pan_y)
    x1 = min(zoomed.shape[1], pan_x + cw)
    y1 = min(zoomed.shape[0], pan_y + ch)
    if x0 < x1 and y0 < y1:
        canvas_y0 = max(0, -pan_y)
        canvas_x0 = max(0, -pan_x)
        canvas[canvas_y0:canvas_y0+(y1-y0), canvas_x0:canvas_x0+(x1-x0)] = zoomed[y0:y1, x0:x1]
    for p in points_original:
        sx = int(p[0]*zoom + pan_x)
        sy = int(p[1]*zoom + pan_y)
        if 0 <= sx < cw and 0 <= sy < ch:
            cv2.circle(canvas, (sx, sy), 6, (0,255,0), -1)
    cv2.imshow("Zoom & Pan Selector", canvas)

# ================= VISUALIZADOR TKINTER COM BOTÃO SALVAR =================
class ResultViewer:
    def __init__(self, img, title="Resultado"):
        self.img = img
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.drag_start = None
        self.root = tk.Tk()
        self.root.title(title)
        self.canvas = tk.Canvas(self.root, width=800, height=800, bg="black")
        self.canvas.pack(fill="both", expand=True)
        self.btn_save = tk.Button(self.root, text="Salvar Imagem", command=self.save_image)
        self.btn_save.pack()
        self.photo = None
        self.canvas.bind("<MouseWheel>", self.zoom)
        self.canvas.bind("<ButtonPress-1>", self.start_pan)
        self.canvas.bind("<B1-Motion>", self.do_pan)
        self.canvas.bind("<Configure>", self.redraw)
        self.redraw()
        self.root.mainloop()

    def zoom(self, event):
        factor = 1.1 if event.delta > 0 else 0.9
        self.scale *= factor
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

    def redraw(self, event=None):
        img = self.img
        if len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        else:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        h, w = img.shape[:2]
        resized = cv2.resize(img, (int(w*self.scale), int(h*self.scale)), interpolation=cv2.INTER_NEAREST)
        self.photo = ImageTk.PhotoImage(Image.fromarray(resized))
        self.canvas.delete("all")
        self.canvas.create_image(self.offset_x, self.offset_y, anchor='nw', image=self.photo)

    def save_image(self):
        path = filedialog.asksaveasfilename(defaultextension='.png', filetypes=[("PNG","*.png"), ("JPEG","*.jpg")])
        if path:
            cv2.imwrite(path, self.img)
            messagebox.showinfo("Salvo", f"Imagem salva em {path}")

# ================= MAIN =================
def main(path):
    global points_original
    if not os.path.exists(path):
        print("Ficheiro não encontrado.")
        return
    img = cv2.imread(path)
    if img is None:
        print("Erro ao carregar imagem.")
        return
    cv2.namedWindow("Zoom & Pan Selector", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("Zoom & Pan Selector", mouse_event, img)
    print("=== CONTROLOS ===")
    print("Scroll → Zoom")
    print("Botão direito + arrastar → Pan")
    print("Botão esquerdo → Selecionar 4 pontos")
    print("ENTER → Confirmar pontos e continuar")
    print("R → Reset dos pontos")
    while True:
        draw_window(img)
        key = cv2.waitKey(10)
        if key == ord('r'):
            points_original = []
            print("Pontos resetados.")
        if key in [13,32] and len(points_original) == 4:
            break
        if key == 27:
            print("Cancelado.")
            return
    cv2.destroyAllWindows()
    prop = input("Proporção L:H (ex:1:1,16:9): ") or "1:1"
    try:
        w_ratio, h_ratio = map(float, prop.split(':'))
    except:
        w_ratio, h_ratio = 1,1
    base = 1000
    out_w = int(ceil(base * (w_ratio / min(w_ratio,h_ratio))))
    out_h = int(ceil(base * (h_ratio / min(w_ratio,h_ratio))))
    warped = four_point_transform(img, points_original, out_w, out_h)
    thr_input = input("Threshold (0-255) ou Enter para Otsu): ")
    gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    if thr_input.strip() == "":
        _, bw = cv2.threshold(gray_warped, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        thresh_val = max(0,min(255,int(thr_input)))
        _, bw = cv2.threshold(gray_warped, thresh_val, 255, cv2.THRESH_BINARY)
    white = np.count_nonzero(bw==255)
    black = np.count_nonzero(bw==0)
    total = white + black
    print(f"Branco: {white/total*100:.2f}% | Preto: {black/total*100:.2f}%")
    ResultViewer(bw, "Imagem Binarizada")

if __name__ == "__main__":
    if len(sys.argv)<2:
        print("Uso: python rectificar.py imagem.jpg")
    else:
        main(sys.argv[1])