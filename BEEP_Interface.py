from tkinter import messagebox
messagebox.showinfo("Startup", "Program has started")

import tkinter as tk
from pymodbus.client import ModbusTcpClient

MODBUS_PORT = 502
UNIT_ID = 255
STATUS_REGISTER_ADDR = 9  # manual 40010


def read_status():
    client = ModbusTcpClient("192.168.0.11", port=MODBUS_PORT)

    if not client.connect():
        messagebox.showerror("Error", "Connection failed")
        return

    rr = client.read_holding_registers(
        address=STATUS_REGISTER_ADDR,
        count=1,
        unit=UNIT_ID
    )

    if rr.isError():
        messagebox.showerror("Modbus Error", str(rr))
    else:
        messagebox.showinfo(
            "Success",
            f"40010 (Status) = {rr.registers[0]}"
        )

    client.close()


root = tk.Tk()
root.title("CFW900 Modbus Test")

btn = tk.Button(root, text="Read 40010 (Status)", command=read_status)
btn.pack(padx=20, pady=20)

root.mainloop()